#!/usr/bin/env python3
"""Compile actor contracts and exercise LiveTyper without keyboard emission."""
import os
from pathlib import Path
import platform
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'tools/dictation-rig/dictate/Sources/quill-dictate'


def run(command, env, *, expected=0):
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=300)
    if result.returncode != expected:
        raise AssertionError(f'{command}: exit {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result


DRIVER = r'''
import Foundation
import Dispatch
@main struct Check {
    @MainActor static func main() {
        var events: [LiveTyper.CheckEvent] = []
        let typer = LiveTyper(checkEmit: { events.append($0) })
        typer.apply(hypothesis: "hello world")
        typer.apply(hypothesis: "hello world")
        typer.apply(hypothesis: "hello there")
        precondition(events == [.text("hello world"), .backspaces(5), .text("there")])
        events = []
        typer.apply(hypothesis: "hello theory")
        precondition(events == [.backspaces(5), .text("theory")])
        MainActor.assumeIsolated { typer.retractAll() }
        events = []
        typer.apply(hypothesis: "👨‍👩‍👧‍👦")
        typer.retractAll()
        precondition(events == [.text("👨‍👩‍👧‍👦"), .backspaces(1)])
        events = []
        typer.apply(hypothesis: "old")
        typer.finalize(with: "new", preservingAppended: "!")
        precondition(events == [.text("old"), .backspaces(4), .text("new!")])
        precondition(typer.typed == "")
        events = []
        typer.apply(hypothesis: "old")
        typer.retractAll(preservingAppended: "!")
        precondition(events == [.text("old"), .backspaces(4), .text("!")])
        precondition(typer.typed == "")
        events = []
        typer.apply(hypothesis: "keep")
        typer.backspaceCharacters(2)
        precondition(typer.typed == "keep")
        precondition(events == [.text("keep"), .backspaces(2)])
        typer.retractAll()
        events = []
        var generation = 1
        var order: [String] = []
        var finished = false
        DispatchQueue.main.async {
            typer.apply(hypothesis: "preview")
            order.append("preview")
        }
        DispatchQueue.main.async {
            generation += 1
            order.append("release")
        }
        DispatchQueue.main.async {
            guard generation == 1 else { order.append("stale dropped"); return }
            typer.apply(hypothesis: "obsolete")
        }
        DispatchQueue.main.async {
            typer.finalize(with: "final", preservingAppended: " suffix")
            precondition(typer.typed == "")
            order.append("final")
        }
        DispatchQueue.main.async {
            precondition(order == ["preview", "release", "stale dropped", "final"])
            precondition(events == [.text("preview"), .backspaces(14), .text("final suffix")])
            order.append("settled")
            finished = true
        }
        let deadline = Date().addingTimeInterval(5)
        while !finished && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
        precondition(finished)
        events = []
        typer.apply(hypothesis: "newer")
        typer.apply(hypothesis: "older")
        precondition(typer.typed == "older")
        precondition(events == [.text("newer"), .backspaces(5), .text("older")])
        precondition(LiveTyper.interpret("old, scratch that, new").kept == "new")
        print("PASS LiveTyper recorded state, graphemes, suffix and main-queue order")
    }
}
'''


def check():
    if platform.system() != 'Darwin':
        print('SKIP LiveTyper requires the macOS Swift SDK')
        return
    with tempfile.TemporaryDirectory(prefix='live-typer-actor-', dir='/tmp') as temporary:
        directory = Path(temporary)
        env = dict(os.environ)
        cache = directory / 'module-cache'
        env['CLANG_MODULE_CACHE_PATH'] = str(cache)
        env['SWIFT_MODULECACHE_PATH'] = str(cache)
        sdk = run(['xcrun', '--show-sdk-path'], env).stdout.strip()
        env['CPLUS_INCLUDE_PATH'] = sdk + '/usr/include/c++/v1'
        live = SOURCES / 'LiveTyper.swift'
        resolver = SOURCES / 'CorrectionResolver.swift'
        compiler = ['swiftc', '-swift-version', '5', '-target', f'{platform.machine()}-apple-macosx14.0',
                    '-sdk', sdk, '-D', 'LIVE_TYPER_CHECK', '-module-cache-path', str(cache)]
        positive = directory / 'Positive.swift'
        positive.write_text('''func pure(_ text: String) {
    _ = LiveTyper.interpret(text)
    _ = LiveTyper.interpretFinal(text)
    _ = LiveTyper.containsCorrectionCue(text)
}
@MainActor func positive() {
    let t = LiveTyper(checkEmit: { _ in })
    t.apply(hypothesis: "a"); t.finalize(with: "b")
    t.retractAll(); t.retractAll(preservingAppended: "c")
    t.backspaceCharacters(1); _ = t.typed
}
''')
        run(compiler + ['-typecheck', str(live), str(resolver), str(positive)], env)
        operations = {'apply': 't.apply(hypothesis: "x")', 'finalize': 't.finalize(with: "x")',
                      'retractAll': 't.retractAll()', 'retractAll preserving': 't.retractAll(preservingAppended: "x")',
                      'backspaceCharacters': 't.backspaceCharacters(1)', 'typed': '_ = t.typed'}
        for name, operation in operations.items():
            probe = directory / 'Negative.swift'
            probe.write_text(f'func negative(_ t: LiveTyper) {{ {operation} }}\n')
            result = subprocess.run(compiler + ['-typecheck', str(live), str(resolver), str(probe)],
                                    env=env, text=True, capture_output=True, timeout=300)
            assert result.returncode != 0 and 'main actor-isolated' in result.stderr, result.stderr
            print(f'PASS actor-negative {name}')
        # A missing individual annotation may fail inside its protected body.
        source = live.read_text()
        declarations = ['@MainActor private(set) var typed:', '@MainActor func apply(',
                        '@MainActor func finalize(', '@MainActor func retractAll()',
                        '@MainActor func retractAll(preservingAppended', '@MainActor func backspaceCharacters(']
        mutation_operations = [operations['typed'], operations['apply'], operations['finalize'],
                               operations['retractAll'], operations['retractAll preserving'],
                               operations['backspaceCharacters']]
        for declaration, operation in zip(declarations, mutation_operations):
            probe.write_text(f'func negative(_ t: LiveTyper) {{ {operation} }}\n')
            mutant = directory / 'LiveTyper.swift'
            mutant.write_text(source.replace(declaration, declaration.replace('@MainActor ', ''), 1))
            result = subprocess.run(compiler + ['-typecheck', str(mutant), str(resolver), str(probe)],
                                    env=env, text=True, capture_output=True, timeout=300)
            if result.returncode == 0:
                print(f'PASS isolation-removal exposes forbidden caller {declaration}')
            else:
                assert 'main actor-isolated' in result.stderr, result.stderr
                print(f'PASS isolation-removal rejected by protected body {declaration}')
        mutant.write_text(source.replace('@MainActor ', ''))
        run(compiler + ['-typecheck', str(mutant), str(resolver), str(probe)], env)
        print('PASS all-isolation removal makes the negative compile')
        missing = directory / 'MissingRecorder.swift'
        missing.write_text('@MainActor func missing() { _ = LiveTyper() }\n')
        result = subprocess.run(compiler + ['-typecheck', str(live), str(resolver), str(missing)],
                                env=env, text=True, capture_output=True, timeout=300)
        assert result.returncode != 0 and "missing argument for parameter 'checkEmit'" in result.stderr
        driver = directory / 'Check.swift'
        driver.write_text(DRIVER)
        executable = directory / 'check'
        run(compiler + ['-parse-as-library', str(live), str(resolver), str(driver), '-o', str(executable)], env)
        print(run([str(executable)], env).stdout.strip())
        for label, changed in [('graphemes', source.replace('postBackspaces(typed.count)', 'postBackspaces(typed.utf16.count)')),
                               ('suffix', source.replace('typed.append(appendedText)', '')),
                               ('word boundary', source.replace('while adjustedL > 0, !typedChars[adjustedL - 1].isWhitespace {', 'while false {'))]:
            mutant.write_text(changed)
            run(compiler + ['-parse-as-library', str(mutant), str(resolver), str(driver), '-o', str(executable)], env)
            result = subprocess.run([str(executable)], env=env, text=True, capture_output=True, timeout=15)
            assert result.returncode != 0, label
            print(f'PASS recorded-state mutant {label}')
        driver.write_text(r"""
import Foundation
import Dispatch
@main struct OffMain {
    @MainActor static func main() {
        let typer = LiveTyper(checkEmit: { _ in })
        DispatchQueue.global().async {
            guard !Thread.isMainThread else { exit(98) }
            FileHandle.standardError.write(Data("off-main before checked entry\n".utf8))
            MainActor.assumeIsolated { typer.retractAll() }
            FileHandle.standardError.write(Data("after checked entry\n".utf8))
            exit(0)
        }
        dispatchMain()
    }
}
""")
        run(compiler + ['-parse-as-library', str(live), str(resolver), str(driver), '-o', str(executable)], env)
        result = subprocess.run([str(executable)], env=env, text=True, capture_output=True, timeout=15)
        assert result.returncode == -signal.SIGTRAP, f'exit={result.returncode} stderr={result.stderr!r}'
        assert 'off-main before checked entry' in result.stderr
        assert 'after checked entry' not in result.stderr
        print('PASS checked bridge refuses off-main executor with SIGTRAP in isolated child')
        driver.write_text(driver.read_text().replace('MainActor.assumeIsolated { typer.retractAll() }', ''))
        run(compiler + ['-parse-as-library', str(live), str(resolver), str(driver), '-o', str(executable)], env)
        result = run([str(executable)], env)
        assert 'after checked entry' in result.stderr
        print('PASS removing checked boundary permits the off-main child to finish')
        changed_driver = DRIVER.replace('typer.finalize(with: "final", preservingAppended: " suffix")',
                                       'DispatchQueue.main.async { typer.finalize(with: "final", preservingAppended: " suffix") }')
        driver.write_text(changed_driver)
        run(compiler + ['-parse-as-library', str(live), str(resolver), str(driver), '-o', str(executable)], env)
        result = subprocess.run([str(executable)], env=env, text=True, capture_output=True, timeout=15)
        assert result.returncode != 0
        print('PASS extra-main-queue-turn mutant')


if __name__ == '__main__':
    check()
