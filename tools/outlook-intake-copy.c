#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#ifdef __APPLE__
#include <sys/clonefile.h>
#endif
#include <sys/wait.h>
#include <unistd.h>

/* The installer binds these paths into the executable granted Full Disk Access. */
#ifndef OUTLOOK_PROFILE
#error OUTLOOK_PROFILE must name the configured fixed profile
#endif
#ifndef OUTLOOK_WORK_ROOT
#error OUTLOOK_WORK_ROOT must name the intake output directory
#endif
#ifndef OUTLOOK_REPO
#error OUTLOOK_REPO must name the checkout whose intake job this binary starts
#endif

static void fail(void) {
    int code = errno ? errno : EIO;
    fprintf(stderr, "outlook-intake-copy: snapshot failed (errno %d)\n", code);
    exit(code);
}

static void require(int condition) {
    if (!condition) {
        errno = EINVAL;
        fail();
    }
}

static int directory(int parent, const char *name) {
    return openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
}

static int make_directory(int parent, const char *name) {
    if (mkdirat(parent, name, 0700) != 0) fail();
    int fd = directory(parent, name);
    if (fd < 0) fail();
    return fd;
}

static int profile_directory(void) {
    char path[] = OUTLOOK_PROFILE;
    require(path[0] == '/');
    int fd = open("/", O_RDONLY | O_DIRECTORY);
    if (fd < 0) fail();
    char *state;
    for (char *part = strtok_r(path, "/", &state); part; part = strtok_r(NULL, "/", &state)) {
        require(strcmp(part, ".") != 0 && strcmp(part, "..") != 0);
        int next = directory(fd, part);
        if (next < 0) fail();
        close(fd);
        fd = next;
    }
    return fd;
}

static off_t copy_file(int source, const char *name, int target, const char *copy, off_t limit, int missing_ok) {
    int reader = openat(source, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (reader < 0 && missing_ok && errno == ENOENT) return -1;
    if (reader < 0) fail();
    struct stat info;
    if (fstat(reader, &info) != 0) fail();
    require(S_ISREG(info.st_mode));
    if (limit && info.st_size > limit) { close(reader); return -1; }
    int writer;
#ifdef __APPLE__
    /* An APFS clone captures the store at one instant even while Outlook
       writes; other volumes fall through to the full copy below. */
    if (fclonefileat(reader, target, copy, CLONE_NOFOLLOW) == 0) {
        writer = openat(target, copy, O_WRONLY | O_NOFOLLOW);
        if (writer < 0) fail();
        if (fchmod(writer, 0600) != 0 || fstat(writer, &info) != 0) fail();
        close(writer);
        close(reader);
        if (!limit || info.st_size <= limit) return info.st_size;
        if (unlinkat(target, copy, 0) != 0) fail();
        return -1;
    }
#endif
    writer = openat(target, copy, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (writer < 0 && limit && errno == EEXIST) {
        if (fstatat(target, copy, &info, AT_SYMLINK_NOFOLLOW) != 0) fail();
        require(S_ISREG(info.st_mode) && info.st_uid == getuid() && (info.st_mode & 0777) == 0600);
        close(reader);
        return info.st_size <= limit ? info.st_size : -1;
    }
    if (writer < 0) fail();
    char buffer[65536];
    ssize_t count;
    off_t total = 0;
    for (;;) {
        count = read(reader, buffer, sizeof buffer);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) fail();
        if (count == 0) break;
        total += count;
        if (limit && total > limit) {
            close(writer);
            close(reader);
            if (unlinkat(target, copy, 0) != 0) fail();
            return -1;
        }
        for (ssize_t offset = 0; offset < count;) {
            ssize_t wrote = write(writer, buffer + offset, (size_t)(count - offset));
            if (wrote < 0 && errno == EINTR) continue;
            if (wrote <= 0) fail();
            offset += wrote;
        }
    }
    if (close(writer) != 0) fail();
    close(reader);
    return total;
}

static DIR *entries(int fd) {
    DIR *list = fdopendir(dup(fd));
    if (!list) fail();
    return list;
}

static struct dirent *next_entry(DIR *list) {
    errno = 0;
    struct dirent *entry = readdir(list);
    if (!entry && errno) fail();
    return entry;
}

static int optional_directory(int parent, const char *name) {
    int fd = directory(parent, name);
    if (fd < 0 && errno != ENOENT) fail();
    return fd;
}

static void copy_sidecars(int profile, int work) {
    int target = make_directory(work, "Files");
    int files = optional_directory(profile, "Files");
    if (files < 0) { close(target); return; }
    int source = optional_directory(files, "S0");
    if (source >= 0) {
        int s0 = make_directory(target, "S0");
        DIR *folders = entries(source);
        struct dirent *folder;
        while ((folder = next_entry(folders))) {
            if (strcmp(folder->d_name, ".") == 0 || strcmp(folder->d_name, "..") == 0) continue;
            struct stat info;
            if (fstatat(source, folder->d_name, &info, AT_SYMLINK_NOFOLLOW) != 0) fail();
            require(!S_ISLNK(info.st_mode));
            if (!S_ISDIR(info.st_mode)) continue;
            int account = directory(source, folder->d_name);
            if (account < 0) fail();
            int efm = optional_directory(account, "EFMData");
            if (efm >= 0) {
                int account_copy = make_directory(s0, folder->d_name);
                int efm_copy = make_directory(account_copy, "EFMData");
                DIR *bodies = entries(efm);
                struct dirent *body;
                while ((body = next_entry(bodies))) {
                    size_t length = strlen(body->d_name);
                    if (length > 4 && strcmp(body->d_name + length - 4, ".dat") == 0)
                        copy_file(efm, body->d_name, efm_copy, body->d_name, 0, 1);
                }
                closedir(bodies);
                close(efm_copy);
                close(account_copy);
                close(efm);
            }
            close(account);
        }
        closedir(folders);
        close(s0);
        close(source);
    }
    close(files);
    close(target);
}

static int private_directory(int parent, const char *name) {
    if (mkdirat(parent, name, 0700) != 0 && errno != EEXIST) fail();
    int fd = directory(parent, name);
    if (fd < 0) fail();
    struct stat info;
    if (fstat(fd, &info) != 0) fail();
    require(info.st_uid == getuid() && (info.st_mode & 0777) == 0700);
    return fd;
}

static int digits(const char *value) {
    if (!*value) return 0;
    for (; *value; value++) if (*value < '0' || *value > '9') return 0;
    return 1;
}

static void copy_attachments(int profile, int work) {
    int files_copy = directory(work, "Files");
    if (files_copy < 0) fail();
    struct stat info;
    if (fstat(files_copy, &info) != 0) fail();
    require(info.st_uid == getuid() && (info.st_mode & 0777) == 0700);
    unsigned count = 0;
    off_t total = 0;
    char line[PATH_MAX];
    while (fgets(line, sizeof line, stdin)) {
        size_t length = strlen(line);
        require(length && line[length - 1] == '\n');
        line[length - 1] = '\0';
        require(strncmp(line, "~/Files/S0/", 11) == 0);
        char *parts[4];
        parts[0] = line + 11;
        for (int i = 1; i < 4; i++) {
            char *slash = strchr(parts[i - 1], '/');
            require(slash != NULL);
            *slash = '\0';
            parts[i] = slash + 1;
        }
        require(digits(parts[0]) && strcmp(parts[1], "Attachments") == 0 && digits(parts[2]));
        require(*parts[3] && !strchr(parts[3], '/') && strcmp(parts[3], ".") != 0 && strcmp(parts[3], "..") != 0);
        for (const unsigned char *p = (unsigned char *)parts[3]; *p; p++) require(*p >= 32 && *p != 127);
        if (count >= 50 || total >= 100 * 1024 * 1024) continue;
        const char *directories[] = {"Files", "S0", parts[0], "Attachments", parts[2]};
        int source = dup(profile);
        if (source < 0) fail();
        for (int i = 0; i < 5 && source >= 0; i++) {
            int next = optional_directory(source, directories[i]);
            close(source);
            source = next;
        }
        if (source < 0) continue;
        int target = dup(files_copy);
        if (target < 0) fail();
        for (int i = 1; i < 5; i++) {
            int next = private_directory(target, directories[i]);
            close(target);
            target = next;
        }
        off_t limit = 100 * 1024 * 1024 - total;
        if (limit > 25 * 1024 * 1024) limit = 25 * 1024 * 1024;
        off_t copied = copy_file(source, parts[3], target, parts[3], limit, 1);
        if (copied >= 0) { count++; total += copied; }
        close(target);
        close(source);
    }
    if (ferror(stdin)) fail();
    close(files_copy);
}

extern char **environ;

/* launchd starts this granted binary directly, so macOS charges the
   whole job's file access to it (a grant on a child process is ignored). Both
   commands are fixed here; callers cannot supply a program or arguments. */
static int run_job(int outbox) {
    char *command[] = {"/bin/zsh", OUTLOOK_REPO "/bin/run-scheduled.sh", outbox ? "outlook-outbox" : "outlook-intake",
                       outbox ? "outlook.outbox" : "outlook.intake", OUTLOOK_REPO "/.venv/bin/python",
                       outbox ? "-m" : "-c", outbox ? "lib.outlook_outbox" :
                       "from lib.outlook_intake import main; raise SystemExit(main())", NULL};
    pid_t child;
    errno = posix_spawn(&child, command[0], NULL, NULL, command, environ);
    if (errno) fail();
    int status;
    while (waitpid(child, &status, 0) < 0)
        if (errno != EINTR) fail();
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--scope") == 0) {
        printf("%s\n%s\n%s\n", OUTLOOK_PROFILE, OUTLOOK_WORK_ROOT, OUTLOOK_REPO);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--run-intake") == 0) return run_job(0);
    if (argc == 2 && strcmp(argv[1], "--run-outbox") == 0) return run_job(1);
    int attachments = argc == 2 && strcmp(argv[1], "--attachments") == 0;
    require(argc == 1 || attachments);
    umask(0077);
    char cwd[PATH_MAX];
    if (!getcwd(cwd, sizeof cwd)) fail();
    char *leaf = strrchr(cwd, '/');
    require(leaf && strncmp(leaf + 1, "work-", 5) == 0 && strlen(leaf + 1) > 5);
    *leaf = '\0';
    require(strcmp(cwd, OUTLOOK_WORK_ROOT) == 0);
    int work = directory(AT_FDCWD, ".");
    if (work < 0) fail();
    struct stat info;
    if (fstat(work, &info) != 0) fail();
    require(info.st_uid == getuid() && (info.st_mode & 0777) == 0700);
    int profile = profile_directory();
    if (attachments) copy_attachments(profile, work);
    else {
        copy_file(profile, "HxStore.hxd", work, "store.hxd", 0, 0);
        copy_sidecars(profile, work);
    }
    close(profile);
    close(work);
    return 0;
}
