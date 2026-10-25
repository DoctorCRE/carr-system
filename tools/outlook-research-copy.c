#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE
#define COMMON_DIGEST_FOR_OPENSSL
#include <CommonCrypto/CommonDigest.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef OUTLOOK_PROFILE
#error OUTLOOK_PROFILE must name the fixed Main Identity profile
#endif
#ifndef OUTLOOK_RESEARCH_ROOT
#error OUTLOOK_RESEARCH_ROOT must name the fixed research directory
#endif

static const char manifest_prefix[] = "{\"schema\":\"outlook-research-snapshot/v1\",\"files\":[";

static int staging_root = -1;
static int staging_work = -1;
static char staging_name[72];
static int remove_contents(int fd);

static void fail(void) {
    int code = errno ? errno : EIO;
    if (staging_work >= 0) {
        remove_contents(staging_work);
        unlinkat(staging_root, staging_name, AT_REMOVEDIR);
    }
    errno = code;
    fprintf(stderr, "outlook-research-copy: snapshot failed (errno %d)\n", errno ? errno : EIO);
    exit(1);
}

static void require(int condition) {
    if (!condition) { errno = EINVAL; fail(); }
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

static int absolute_directory(const char *fixed, int create_last) {
    char path[PATH_MAX];
    require(strlen(fixed) < sizeof path && fixed[0] == '/');
    strcpy(path, fixed);
    int fd = directory(AT_FDCWD, "/");
    if (fd < 0) fail();
    char *state;
    for (char *part = strtok_r(path, "/", &state); part;) {
        char *next_part = strtok_r(NULL, "/", &state);
        require(strcmp(part, ".") != 0 && strcmp(part, "..") != 0);
        if (!next_part && create_last && mkdirat(fd, part, 0700) != 0 && errno != EEXIST) fail();
        int next = directory(fd, part);
        if (next < 0) fail();
        close(fd);
        fd = next;
        part = next_part;
    }
    return fd;
}

static void private_directory(int fd) {
    struct stat info;
    if (fstat(fd, &info) != 0) fail();
    require(info.st_uid == getuid() && (info.st_mode & 0777) == 0700);
}

static DIR *entries(int fd) {
    DIR *list = fdopendir(directory(fd, "."));
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

static void json_string(FILE *manifest, const char *value) {
    fputc('"', manifest);
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p == '"' || *p == '\\') fputc('\\', manifest);
        if (*p < 32) fprintf(manifest, "\\u%04x", *p);
        else fputc(*p, manifest);
    }
    fputc('"', manifest);
}

static void copy_file(int source, const char *name, int target, const char *path,
                      FILE *manifest, int *first) {
    int reader = openat(source, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (reader < 0) fail();
    struct stat before, after;
    if (fstat(reader, &before) != 0) fail();
    require(S_ISREG(before.st_mode));
    int writer = openat(target, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (writer < 0) fail();
    CC_SHA256_CTX hash;
    CC_SHA256_Init(&hash);
    char buffer[65536];
    off_t total = 0;
    for (;;) {
        ssize_t count = read(reader, buffer, sizeof buffer);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) fail();
        if (count == 0) break;
        CC_SHA256_Update(&hash, buffer, (CC_LONG)count);
        total += count;
        for (ssize_t offset = 0; offset < count;) {
            ssize_t wrote = write(writer, buffer + offset, (size_t)(count - offset));
            if (wrote < 0 && errno == EINTR) continue;
            if (wrote <= 0) fail();
            offset += wrote;
        }
    }
    if (fstat(reader, &after) != 0) fail();
    require(total == before.st_size && before.st_size == after.st_size &&
            before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec &&
            before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec &&
            before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec &&
            before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec);
    if (fsync(writer) != 0 || close(writer) != 0) fail();
    close(reader);
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &hash);
    if (!*first) fputc(',', manifest);
    *first = 0;
    fputs("{\"path\":", manifest);
    json_string(manifest, path);
    fprintf(manifest, ",\"size\":%lld,\"mtime_ns\":%lld,\"sha256\":\"",
            (long long)before.st_size,
            (long long)before.st_mtimespec.tv_sec * 1000000000LL + before.st_mtimespec.tv_nsec);
    for (size_t i = 0; i < sizeof digest; ++i) fprintf(manifest, "%02x", digest[i]);
    fputs("\"}", manifest);
    if (ferror(manifest)) fail();
}

static void copy_sidecars(int profile, int work, FILE *manifest, int *first) {
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
                    if (length > 4 && strcmp(body->d_name + length - 4, ".dat") == 0) {
                        char path[PATH_MAX];
                        int n = snprintf(path, sizeof path, "Files/S0/%s/EFMData/%s", folder->d_name, body->d_name);
                        require(n > 0 && (size_t)n < sizeof path);
                        copy_file(efm, body->d_name, efm_copy, path, manifest, first);
                    }
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

static int snapshot_name(const char *name) {
    if (strlen(name) != 31 || strncmp(name, "snap-", 5) != 0 ||
        name[13] != 'T' || name[20] != '-' || name[30] != 'Z') return 0;
    for (size_t i = 5; i < 30; ++i)
        if (i != 13 && i != 20 && (name[i] < '0' || name[i] > '9')) return 0;
    return 1;
}

static int own_snapshot(int root, const char *name) {
    if (!snapshot_name(name)) return 0;
    int fd = directory(root, name);
    if (fd < 0) return 0;
    struct stat info;
    int own = fstat(fd, &info) == 0 && info.st_uid == getuid() && (info.st_mode & 0777) == 0700;
    int manifest = openat(fd, "manifest.json", O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (manifest >= 0) {
        char prefix[sizeof manifest_prefix - 1];
        own = own && fstat(manifest, &info) == 0 && S_ISREG(info.st_mode) &&
              info.st_uid == getuid() && info.st_nlink == 1 && (info.st_mode & 0777) == 0600 &&
              read(manifest, prefix, sizeof prefix) == sizeof prefix &&
              memcmp(prefix, manifest_prefix, sizeof prefix) == 0;
        close(manifest);
    } else own = 0;
    close(fd);
    return own;
}

static int remove_contents(int fd) {
    DIR *list = fdopendir(directory(fd, "."));
    if (!list) return -1;
    int status = 0;
    struct dirent *entry;
    for (;;) {
        errno = 0;
        entry = readdir(list);
        if (!entry) { if (errno) status = -1; break; }
        const char *name = entry->d_name;
        if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) continue;
        struct stat info;
        if (fstatat(fd, name, &info, AT_SYMLINK_NOFOLLOW) != 0 || info.st_uid != getuid()) {
            status = -1; break;
        }
        if (S_ISDIR(info.st_mode)) {
            int child = directory(fd, name);
            if (child < 0) { status = -1; break; }
            status = remove_contents(child);
            close(child);
            if (status != 0 || unlinkat(fd, name, AT_REMOVEDIR) != 0) { status = -1; break; }
        } else if (unlinkat(fd, name, 0) != 0) { status = -1; break; }
    }
    closedir(list);
    return status;
}

static void prune(int root, const char *new_name) {
    for (;;) {
        size_t count = 0;
        char oldest[32] = "";
        DIR *list = entries(root);
        struct dirent *entry;
        while ((entry = next_entry(list))) {
            if (!own_snapshot(root, entry->d_name)) continue;
            require(strcmp(new_name, entry->d_name) > 0);
            ++count;
            if (!*oldest || strcmp(entry->d_name, oldest) < 0) strcpy(oldest, entry->d_name);
        }
        closedir(list);
        if (count < 20) return;
        char retired[48];
        snprintf(retired, sizeof retired, ".%s.retired", oldest);
        if (renameatx_np(root, oldest, root, retired, RENAME_EXCL) != 0) fail();
        int fd = directory(root, retired);
        if (fd < 0 || remove_contents(fd) != 0) fail();
        close(fd);
        if (unlinkat(root, retired, AT_REMOVEDIR) != 0) fail();
    }
}

int main(int argc, char **argv) {
    (void)argv;
    require(argc == 1);
    umask(0077);
    int root = absolute_directory(OUTLOOK_RESEARCH_ROOT, 1);
    private_directory(root);
    int lock = openat(root, ".lock", O_WRONLY | O_CREAT | O_NOFOLLOW | O_NONBLOCK, 0600);
    if (lock < 0) fail();
    struct stat info;
    if (fstat(lock, &info) != 0) fail();
    require(S_ISREG(info.st_mode) && info.st_uid == getuid() && info.st_nlink == 1 &&
            (info.st_mode & 0777) == 0600);
    if (flock(lock, LOCK_EX | LOCK_NB) != 0) fail();
    int profile = absolute_directory(OUTLOOK_PROFILE, 0);
    struct timespec now;
    struct tm utc;
    if (clock_gettime(CLOCK_REALTIME, &now) != 0 || !gmtime_r(&now.tv_sec, &utc)) fail();
    char stamp[32], name[64], pending[72];
    require(strftime(stamp, sizeof stamp, "%Y%m%dT%H%M%S", &utc) != 0);
    snprintf(name, sizeof name, "snap-%s-%09ldZ", stamp, now.tv_nsec);
    snprintf(pending, sizeof pending, ".%s.pending", name);
    int work = make_directory(root, pending);
    staging_root = root;
    staging_work = work;
    strcpy(staging_name, pending);
    int fd = openat(work, "manifest.json", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) fail();
    FILE *manifest = fdopen(fd, "w");
    if (!manifest) fail();
    fputs(manifest_prefix, manifest);
    int first = 1;
    copy_file(profile, "HxStore.hxd", work, "HxStore.hxd", manifest, &first);
    copy_sidecars(profile, work, manifest, &first);
    fputs("]}\n", manifest);
    if (fflush(manifest) != 0 || fsync(fd) != 0 || fclose(manifest) != 0) fail();
    prune(root, name);
    if (renameatx_np(root, pending, root, name, RENAME_EXCL) != 0) fail();
    staging_work = -1;
    close(profile);
    close(work);
    close(lock);
    close(root);
    return 0;
}
