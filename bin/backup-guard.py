#!/usr/bin/env python3
"""Keep coverage, the exported snapshot and encryption in one backup lifetime.

The backup role stays read-only. SQL is native pg_dump output: the observer
never rewrites it. Verbose TABLE TOC OIDs witness pg_dump's actual selection;
they do not replace RLS coverage or prove atomic sequence state.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


class BackupError(RuntimeError):
    pass


# Both census calls have one statement snapshot. Only the second exports it,
# on the original top-level READ COMMITTED transaction (never a savepoint).
CENSUS = """
with relations as materialized (
  select c.*, n.nspname, pg_get_userbyid(c.relowner) as owner_name
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname in ('public','ops')
), tables as materialized (
  select * from relations where relkind in ('r','p')
), edges as materialized (
  select i.inhrelid::bigint child, i.inhparent::bigint parent,
         i.inhseqno, i.inhdetachpending,
         cn.nspname child_schema, pn.nspname parent_schema
  from pg_inherits i
  join pg_class cc on cc.oid=i.inhrelid
  join pg_namespace cn on cn.oid=cc.relnamespace
  join pg_class pc on pc.oid=i.inhparent
  join pg_namespace pn on pn.oid=pc.relnamespace
  where i.inhrelid in (select oid from tables)
     or i.inhparent in (select oid from tables)
), topology as (
  select coalesce(jsonb_agg(to_jsonb(e) order by child,parent,inhseqno),'[]'::jsonb) value
  from edges e
)
select jsonb_build_object(
  'pid',pg_backend_pid(), 'current_user',current_user,'session_user',session_user,
  'readonly',current_setting('transaction_read_only'),
  'isolation',current_setting('transaction_isolation'),
  'elevated',coalesce((select rolsuper or rolbypassrls from pg_roles where rolname=current_user),true),
  'owns_database',exists(select 1 from pg_database where datname=current_database()
                         and pg_has_role(current_user,datdba,'USAGE')),
  'owns_schema',exists(select 1 from pg_namespace where nspname in ('public','ops')
                       and pg_has_role(current_user,nspowner,'USAGE')),
  'owns_relation',exists(select 1 from relations where relkind in ('r','p','S','v','m','f')
                         and pg_has_role(current_user,relowner,'USAGE')),
  'unsupported',coalesce((select jsonb_agg(oid::bigint) from relations where relkind in ('m','f')),'[]'::jsonb),
  'large_objects',exists(select 1 from pg_largeobject_metadata),
  'tables',coalesce((select jsonb_agg(jsonb_build_object(
    'oid',t.oid::bigint,'schema',t.nspname,'name',t.relname,'kind',t.relkind,
    'rls',t.relrowsecurity,'select',has_table_privilege(current_user,t.oid,'SELECT'),
    'tablespace',coalesce((select spcname from pg_tablespace where oid=t.reltablespace),''),
    'policies',coalesce((select jsonb_agg(jsonb_build_object(
      'permissive',p.polpermissive,'command',p.polcmd,
      'direct',0::oid=any(p.polroles) or
         (select oid from pg_roles where rolname=current_user)=any(p.polroles),
      'using',pg_get_expr(p.polqual,p.polrelid)))
      from pg_policy p where p.polrelid=t.oid and p.polcmd in ('r','*')),'[]'::jsonb)
    ) order by t.oid) from tables t),'[]'::jsonb),
  'sequences',coalesce((select jsonb_agg(jsonb_build_object(
    'oid',oid::bigint,'select',has_sequence_privilege(current_user,oid,'SELECT')))
    from relations where relkind='S'),'[]'::jsonb),
  'topology',(select value from topology),
  'same_oids',%s::oid[] is null or
     array(select oid from tables order by oid)=%s::oid[],
  'same_topology',%s::jsonb is null or (select value from topology)=%s::jsonb,
  'snapshot',case when %s then pg_export_snapshot() else null end
)
"""


def bounded_env(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise BackupError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise BackupError(f"{name} must be positive and at most {maximum:g}")
    return value


def literal_true(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    while text.startswith('(') and text.endswith(')'):
        text = text[1:-1].strip()
    return text.lower() == 'true'


class Guard:
    def __init__(self, dsn: str, lock_ms: int):
        # Override disabled or unbounded URI settings; the same normalized DSN
        # goes to pg_dump. It is never placed in the process argument list.
        self.dsn = make_conninfo(
            dsn, connect_timeout=int(bounded_env('BACKUP_CONNECT_TIMEOUT', 30, 120)),
            keepalives=1,
            keepalives_idle=int(bounded_env('BACKUP_KEEPALIVE_IDLE', 60, 120)),
            keepalives_interval=int(bounded_env('BACKUP_KEEPALIVE_INTERVAL', 15, 30)),
            keepalives_count=int(bounded_env('BACKUP_KEEPALIVE_COUNT', 12, 12)),
        )
        self.lock_ms = lock_ms
        self.conn = None
        self.tables: list[dict] = []
        self.pid = 0
        self.snapshot = ''

    @staticmethod
    def validate(census: dict) -> None:
        if (census['current_user'] != 'carr_backup' or
                census['session_user'] != 'carr_backup' or census['elevated'] or
                census['owns_database'] or census['owns_schema'] or census['owns_relation']):
            raise BackupError('routine backup requires the non-owner, non-BYPASSRLS carr_backup login')
        if census['readonly'] != 'on' or census['isolation'] != 'read committed':
            raise BackupError('original read-only READ COMMITTED guard transaction is required')
        if census['unsupported'] or census['large_objects']:
            raise BackupError('materialized/foreign relations or omitted large objects require explicit backup support')
        if not census['same_oids'] or not census['same_topology']:
            raise BackupError('relation or partition census changed while acquiring locks')
        for edge in census['topology']:
            if (edge['child_schema'] not in ('public', 'ops') or
                    edge['parent_schema'] not in ('public', 'ops') or edge['inhdetachpending']):
                raise BackupError('cross-scope or pending partition/inheritance topology is unsupported')
        for table in census['tables']:
            if not table['select']:
                raise BackupError(f"whole-table SELECT missing for OID {table['oid']}")
            if table['rls']:
                policies = table['policies']
                if not any(p['permissive'] and p['direct'] and literal_true(p['using']) for p in policies):
                    raise BackupError(f"literal-true backup read policy missing for OID {table['oid']}")
                if any(not p['permissive'] and not literal_true(p['using']) for p in policies):
                    raise BackupError(f"restrictive policy can filter backup OID {table['oid']}")
        if any(not sequence['select'] for sequence in census['sequences']):
            raise BackupError('sequence SELECT missing; native sequence backup cannot be complete')

    def __enter__(self):
        try:
            self.conn = psycopg.connect(self.dsn, autocommit=True, application_name='carr-backup-guard')
            self.conn.execute('BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY')
            self.conn.execute("select set_config('lock_timeout',%s,true), set_config('statement_timeout',%s,true)",
                              (f'{self.lock_ms}ms', f'{max(self.lock_ms + 1000, 5000)}ms'))
            initial = self.conn.execute(CENSUS, (None, None, None, None, False)).fetchone()[0]
            self.validate(initial)
            self.pid = initial['pid']
            self.tables = initial['tables']
            for table in self.tables:
                self.conn.execute(sql.SQL('LOCK TABLE ONLY {}.{} IN ACCESS SHARE MODE').format(
                    sql.Identifier(table['schema']), sql.Identifier(table['name'])))
            oids = [t['oid'] for t in self.tables]
            topology = json.dumps(initial['topology'])
            final = self.conn.execute(CENSUS, (oids, oids, topology, topology, True)).fetchone()[0]
            self.validate(final)
            self.tables = final['tables']
            self.snapshot = final['snapshot']
            if not self.snapshot or final['pid'] != self.pid:
                raise BackupError('snapshot export lost the original guard')
            self.ack()
            return self
        except BaseException:
            self.close()
            raise

    def ack(self) -> None:
        if self.conn is None or self.conn.closed or self.conn.broken:
            raise BackupError('original backup guard connection was lost')
        row = self.conn.execute("""
          select pg_backend_pid(),current_user,session_user,
                 current_setting('transaction_read_only'),
                 array(select relation::bigint from pg_locks
                        where pid=pg_backend_pid() and granted and mode='AccessShareLock'
                          and database=(select oid from pg_database where datname=current_database())
                          and relation=any(%s::oid[]) order by relation)
        """, ([t['oid'] for t in self.tables],)).fetchone()
        if (row[0] != self.pid or row[1:4] != ('carr_backup', 'carr_backup', 'on') or
                list(row[4]) != [t['oid'] for t in self.tables]):
            raise BackupError('original guard identity or relation locks were lost')

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()  # rolls back the explicit transaction; never reconnects

    def __exit__(self, *_):
        self.close()


TOC = re.compile(rb'^-- TOC entry ([0-9]+) \(class ([0-9]+) OID ([0-9]+)\)\r?\n?$')
DOLLAR = re.compile(rb'\$(?:[A-Za-z_\x80-\xff][A-Za-z_0-9\x80-\xff]*)?\$')
WORD = re.compile(rb'[A-Za-z_][A-Za-z_0-9$]*')


def sanitize_header(text: str) -> bytes:
    # PostgreSQL sanitize_line removes physical line breaks from TOC metadata.
    return text.replace('\n', ' ').replace('\r', ' ').encode('utf-8')


class DumpObserver:
    """An observe-only SQL lexer: bytes are forwarded by the caller unchanged."""
    def __init__(self, tables: list[dict]):
        self.expected = {t['oid']: t for t in tables}
        self.seen: set[int] = set()
        self.state = 'sql'
        self.depth = 0
        self.tag = b''
        self.escape = False
        self.tokens: list[bytes] = []
        self.pending: tuple[int, int] | None = None

    def metadata(self, line: bytes) -> None:
        match = TOC.fullmatch(line)
        if match:
            if self.pending is not None:
                raise BackupError('TOC entry has no unambiguous type header')
            self.pending = (int(match[2]), int(match[3]))
            return
        if line.startswith(b'-- TOC entry'):
            raise BackupError('malformed TOC metadata')
        if self.pending is None:
            return
        if line.startswith(b'-- Dependencies:'):
            if not re.fullmatch(rb'-- Dependencies:(?: [0-9]+)*\r?\n?', line):
                raise BackupError('malformed TOC dependencies')
            return
        if not line.startswith(b'-- Name: ') and not line.startswith(b'-- Data for Name: '):
            raise BackupError('TOC type header missing')
        catalog, oid = self.pending
        self.pending = None
        if catalog != 1259:
            return
        table = self.expected.get(oid)
        if table is not None:
            expected = (b'-- Name: ' + sanitize_header(table['name']) +
                        b'; Type: TABLE; Schema: ' + table['schema'].encode() + b'; Owner: -')
            if table.get('tablespace'):
                expected += b'; Tablespace: ' + sanitize_header(table['tablespace'])
            if line.rstrip(b'\r\n') != expected:
                raise BackupError('locked TABLE has ambiguous or wrong TOC type/header')
            if oid in self.seen:
                raise BackupError('duplicate TABLE OID in actual dump')
            self.seen.add(oid)
        elif b'; Type: TABLE;' in line:
            # Unknown/ambiguous class-1259 TABLE metadata cannot broaden scope.
            raise BackupError('actual dump contains a TABLE outside the locked census')

    def feed(self, line: bytes) -> None:
        if b'\x00' in line:
            raise BackupError('NUL in native SQL stream')
        if self.state == 'copy':
            if line in (b'\\.\n', b'\\.\r\n'):
                self.state = 'sql'
            return
        if self.state == 'sql' and not self.tokens:
            if line.startswith(b'--'):
                self.metadata(line)
                return
            if self.pending is not None:
                raise BackupError('truncated TOC header')
            if line.startswith(b'\\'):
                if not re.fullmatch(rb'\\(?:unrestrict|restrict) [A-Za-z0-9]+\r?\n?', line):
                    raise BackupError('unsupported psql command in dump')
                return
        i = 0
        while i < len(line):
            if self.state == 'dollar':
                end = line.find(self.tag, i)
                if end < 0:
                    return
                i = end + len(self.tag)
                self.state = 'sql'
                continue
            if self.state == 'block':
                if line[i:i+2] == b'/*':
                    self.depth += 1
                    i += 2
                elif line[i:i+2] == b'*/':
                    self.depth -= 1
                    i += 2
                    if self.depth == 0:
                        self.state = 'sql'
                else:
                    i += 1
                continue
            if self.state in ('string', 'identifier'):
                quote = 39 if self.state == 'string' else 34
                if self.state == 'string' and self.escape and line[i] == 92:
                    i += 2
                elif line[i] == quote:
                    if i + 1 < len(line) and line[i + 1] == quote:
                        i += 2
                    else:
                        self.state = 'sql'
                        i += 1
                else:
                    i += 1
                continue
            pair = line[i:i+2]
            if pair == b'--':
                return
            if pair == b'/*':
                self.state, self.depth = 'block', 1
                i += 2
                continue
            byte = line[i]
            if byte in (39, 34):
                self.escape = byte == 39 and i > 0 and line[i-1:i].lower() == b'e'
                self.state = 'string' if byte == 39 else 'identifier'
                self.tokens.append(b'<quoted>')
                i += 1
                continue
            if byte == 36:
                match = DOLLAR.match(line, i)
                if match:
                    self.tag, self.state = match[0], 'dollar'
                    self.tokens.append(b'<quoted>')
                    i = match.end()
                    continue
            if byte == 59:
                is_copy = self.tokens[:1] == [b'COPY'] and self.tokens[-2:] == [b'FROM', b'STDIN']
                self.tokens.clear()
                if is_copy:
                    if line[i+1:].strip():
                        raise BackupError('ambiguous COPY header')
                    self.state = 'copy'
                    return
                i += 1
                continue
            match = WORD.match(line, i)
            if match:
                self.tokens.append(match[0].upper())
                # Only first and final tokens determine COPY. Bound statements
                # containing millions of literal values without retaining SQL.
                if len(self.tokens) > 16:
                    self.tokens = self.tokens[:1] + self.tokens[-4:]
                i = match.end()
            else:
                i += 1

    def finish(self) -> None:
        if self.state != 'sql' or self.tokens or self.pending is not None:
            raise BackupError('truncated SQL/COPY/TOC lexical state')
        if self.seen != set(self.expected):
            missing = sorted(set(self.expected) - self.seen)
            extra = sorted(self.seen - set(self.expected))
            raise BackupError(f'actual dump TABLE OIDs differ from the locked census: missing={missing}, extra={extra}')


def stop_child(child: subprocess.Popen | None) -> None:
    # Nothing here may raise past this function: it runs from
    # encrypted_dump()'s finally block, where an escaping exception would
    # skip the cleanup steps after it (in particular temporary.unlink(),
    # which must always run) and can replace/mask the real BackupError the
    # caller is already propagating. Besides ProcessLookupError (the pid
    # is already gone), os.killpg can also raise PermissionError -- e.g. if
    # the pid was reaped and reused between our poll() check and the
    # signal, or under a sandboxing layer that restricts signalling -- so
    # every OS-level failure here is caught broadly (OSError) and reported
    # rather than left to propagate.
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except OSError as exc:
        print(f'backup-guard: could not SIGTERM child pid {child.pid}: {exc}', file=sys.stderr)
    try:
        child.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except OSError as exc:
        print(f'backup-guard: could not SIGKILL child pid {child.pid}: {exc}', file=sys.stderr)
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Under real system load, reaping a just-SIGKILL'd child can still
        # time out. Report it rather than silently swallowing a child we
        # could not confirm dead.
        print(f'backup-guard: could not reap child pid {child.pid} after SIGKILL',
              file=sys.stderr)


def dump_connection(dsn: str) -> tuple[dict[str, str], str]:
    # PGDATABASE is a database name, not an expanded connection string. Use
    # libpq's own environment mapping to keep credentials out of argv.
    mapping = {item.keyword.decode(): item.envvar.decode()
               for item in psycopg.pq.Conninfo.get_defaults() if item.envvar}
    env = dict(os.environ)
    options = conninfo_to_dict(make_conninfo(dsn, application_name='pg_dump'))
    public: dict[str, str] = {}
    public_keys = {'keepalives', 'keepalives_idle', 'keepalives_interval',
                   'keepalives_count', 'tcp_user_timeout'}
    for key, value in options.items():
        if not isinstance(value, str):
            raise BackupError(f'connection option must be text: {key}')
        if key in mapping:
            env[mapping[key]] = value
        elif key in public_keys:
            public[key] = value
        else:
            raise BackupError(f'connection option cannot be passed privately to pg_dump: {key}')
    # libpq exposes no environment variables for these nonsecret timeouts.
    return env, make_conninfo(**public)


def encrypted_dump(guard: Guard, output: Path, recipient: str, pg_dump: str,
                   lock_ms: int, poll: float) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{output.name}.', suffix='.tmp', dir=output.parent)
    temporary = Path(name)
    dump = age = None
    pump = None
    done = threading.Event()
    errors: list[BaseException] = []
    observer = DumpObserver(guard.tables)
    primary_raised = False
    try:
        with os.fdopen(fd, 'wb') as ciphertext, tempfile.TemporaryFile() as dump_err, tempfile.TemporaryFile() as age_err:
            env, public_connection = dump_connection(guard.dsn)
            dump = subprocess.Popen([
                pg_dump, '--no-owner', '--no-acl', '--enable-row-security',
                '--schema=public', '--schema=ops', '--format=plain', '--verbose',
                f'--dbname={public_connection}',
                f'--snapshot={guard.snapshot}', f'--lock-wait-timeout={lock_ms}ms',
            ], stdout=subprocess.PIPE, stderr=dump_err, env=env, start_new_session=True)
            age = subprocess.Popen(['age', '-r', recipient], stdin=subprocess.PIPE,
                                   stdout=ciphertext, stderr=age_err, start_new_session=True)

            def transfer() -> None:
                try:
                    assert dump is not None and dump.stdout is not None
                    assert age is not None and age.stdin is not None
                    while True:
                        line = dump.stdout.readline(64 * 1024 * 1024 + 1)
                        if not line:
                            break
                        if len(line) > 64 * 1024 * 1024:
                            raise BackupError('SQL line exceeds bounded observer capacity')
                        observer.feed(line)
                        age.stdin.write(line)  # observe only, byte-for-byte passthrough
                    observer.finish()
                    age.stdin.close()
                    if dump.wait() != 0 or age.wait() != 0:
                        raise BackupError('pg_dump or age failed')
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    done.set()

            pump = threading.Thread(target=transfer, name='backup-stream', daemon=True)
            pump.start()
            while not done.wait(poll):
                guard.ack()
            if errors:
                raise BackupError(f'dump stream refused: {errors[0]}')
            pump.join()
            ciphertext.flush()
            os.fsync(ciphertext.fileno())
            size = temporary.stat().st_size
            prior = sorted((p for p in output.parent.glob('carr-*.sql.age') if p != output),
                           key=lambda p: p.stat().st_mtime_ns, reverse=True)
            floor = max(1048576, prior[0].stat().st_size // 2 if prior else 0)
            if size < floor:
                raise BackupError(f'SHORT DUMP: {size} bytes below floor {floor}')
            # Last acknowledgement is on the original connection with all its
            # locks, after encryption/floor and immediately before replacement.
            guard.ack()
            os.replace(temporary, output)
            return {'file': str(output), 'bytes': size, 'floor': floor,
                    'table_count': len(observer.seen)}
    except BaseException:
        primary_raised = True
        raise
    finally:
        # Every cleanup step runs, unconditionally, and an ORDINARY failure
        # in one of them may never replace whatever the `try` block above
        # is already propagating (a BackupError, or a clean return). A bare
        # nested try/finally does NOT give that guarantee: if one of these
        # steps raises while an earlier exception is already unwinding,
        # Python has the new exception replace/mask the original -- exactly
        # the failure mode this is guarding against, just moved one level
        # up. stop_child() itself is hardened not to raise for ordinary
        # OS-level failures (see its own comment), but this is the second,
        # independent line of defense for anything unexpected.
        #
        # A SIGNAL is different. main()'s handler turns SIGALRM/SIGTERM/
        # SIGINT into a raised BackupError/KeyboardInterrupt, and that can
        # land inside stop_child()'s blocking wait(timeout=2) — interrupting
        # it before SIGKILL is sent, so the child is left unkilled. If that
        # were logged and swallowed like an ordinary cleanup failure, a
        # termination signal arriving after a successful dump would still
        # report success. So a BackupError/KeyboardInterrupt (and, for the
        # same reason, SystemExit/GeneratorExit -- see cleanup_step) caught
        # here is remembered and, once every cleanup step has still been
        # run, is re-raised -- but ONLY when `primary_raised` is False, i.e.
        # nothing from the `try`/`except BaseException` above is already
        # propagating: a primary BackupError always wins over one raised
        # during cleanup. This must be the explicit `primary_raised` flag
        # set by that `except BaseException`, NOT sys.exc_info(): inside a
        # finally block, sys.exc_info() also reports an exception a CALLER
        # is already handling in an enclosing except block, even when this
        # function's own try body returned cleanly -- so checking it here
        # would swallow a cleanup signal (and report success) whenever
        # encrypted_dump() happens to be invoked from inside someone else's
        # except clause, which has nothing to do with this call's outcome.
        cleanup_failures: list[tuple[str, BaseException]] = []
        cleanup_signal: BaseException | None = None
        signal_classes = (BackupError, KeyboardInterrupt, SystemExit, GeneratorExit)

        def cleanup_step(description: str, action) -> None:
            nonlocal cleanup_signal
            try:
                action()
            except signal_classes as exc:
                if cleanup_signal is None:
                    cleanup_signal = exc
                cleanup_failures.append((description, exc))
            except Exception as exc:
                cleanup_failures.append((description, exc))

        cleanup_step('stop dump child', lambda: stop_child(dump))
        cleanup_step('stop age child', lambda: stop_child(age))
        cleanup_step('join transfer thread',
                     lambda: pump.join(timeout=2) if pump is not None else None)
        cleanup_step('remove private temp file',
                     lambda: temporary.unlink(missing_ok=True))

        for description, exc in cleanup_failures:
            print(f'backup-guard: cleanup step failed ({description}): {exc!r}',
                  file=sys.stderr)

        if cleanup_signal is not None and not primary_raised:
            raise cleanup_signal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--recipient', required=True)
    parser.add_argument('--pg-dump', default='pg_dump')
    args = parser.parse_args()
    try:
        timeout = bounded_env('BACKUP_TIMEOUT_SECONDS', 900, 3600)
        lock_ms = max(1, int(bounded_env('BACKUP_LOCK_TIMEOUT_MS', 10000, 60000)))
        poll = max(0.01, bounded_env('BACKUP_POLL_SECONDS', 0.2, 1))
        dsn = os.environ.get('CARR_DB_BACKUP_URL') or os.environ.get('BACKUP_DATABASE_URL')
        if not dsn:
            raise BackupError('CARR_DB_BACKUP_URL or BACKUP_DATABASE_URL is required')

        def interrupted(signum, _frame):
            raise BackupError('backup deadline exceeded' if signum == signal.SIGALRM else 'backup interrupted')

        signal.signal(signal.SIGALRM, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            with Guard(dsn, lock_ms) as guard:
                result = encrypted_dump(guard, args.output, args.recipient, args.pg_dump, lock_ms, poll)
            print(json.dumps(result))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return 0
    except (BackupError, psycopg.Error, OSError, ValueError) as exc:
        print(f'backup-guard: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
