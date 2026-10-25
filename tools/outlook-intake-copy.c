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

static void copy_file(int source, const char *name, int target, const char *copy) {
    int reader = openat(source, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (reader < 0) fail();
    struct stat info;
    if (fstat(reader, &info) != 0) fail();
    require(S_ISREG(info.st_mode));
    int writer = openat(target, copy, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (writer < 0) fail();
    char buffer[65536];
    ssize_t count;
    for (;;) {
        count = read(reader, buffer, sizeof buffer);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) fail();
        if (count == 0) break;
        for (ssize_t offset = 0; offset < count;) {
            ssize_t wrote = write(writer, buffer + offset, (size_t)(count - offset));
            if (wrote < 0 && errno == EINTR) continue;
            if (wrote <= 0) fail();
            offset += wrote;
        }
    }
    if (close(writer) != 0) fail();
    close(reader);
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
                        copy_file(efm, body->d_name, efm_copy, body->d_name);
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

extern char **environ;

/* launchd starts this granted binary with "--run-intake", so macOS charges the
   whole job's file access to it (a grant on a child process is ignored). The
   command is fixed here; the grant never extends to anything else. */
static int run_intake(void) {
    char *command[] = {"/bin/zsh", OUTLOOK_REPO "/bin/run-scheduled.sh", "outlook-intake",
                       "outlook.intake", OUTLOOK_REPO "/.venv/bin/python", "-c",
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
    if (argc == 2 && strcmp(argv[1], "--run-intake") == 0) return run_intake();
    require(argc == 1);
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
    copy_file(profile, "HxStore.hxd", work, "store.hxd");
    copy_sidecars(profile, work);
    close(profile);
    close(work);
    return 0;
}
