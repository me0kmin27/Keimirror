/* Test-only scheduling injection, not a fake proc file or signal mask.
 * Bash read buffers a proc status and seeks backwards after each line. Make
 * its sleeping parent stop just before the seek after SigBlk: regenerating
 * status shortens State: S (sleeping) to T (stopped), shifting SigIgn by one.
 * All reads/seeks still go to the real kernel; no disposition is changed.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static ssize_t (*native_read)(int, void *, size_t);
static off_t (*native_lseek)(int, off_t, int);
static pid_t parent;
static int pending;

ssize_t read(int fd, void *buffer, size_t size) {
    if (!native_read) native_read = dlsym(RTLD_NEXT, "read");
    ssize_t count = native_read(fd, buffer, size);
    int saved = errno;
    if (fd == 0 && count > 7 && !memcmp(buffer, "SigBlk:", 7)) {
        char path[80], target[128];
        snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
        ssize_t length = readlink(path, target, sizeof(target) - 1);
        if (length > 0) {
            target[length] = 0;
            int pid = 0;
            if (sscanf(target, "/proc/%d/status", &pid) == 1 && pid == getppid()) {
                parent = pid;
                pending = 1;
            }
        }
    }
    errno = saved;
    return count;
}

off_t lseek(int fd, off_t offset, int whence) {
    if (!native_lseek) native_lseek = dlsym(RTLD_NEXT, "lseek");
    if (fd == 0 && offset < 0 && whence == SEEK_CUR && pending) {
        pending = 0;
        if (parent != getppid() || kill(parent, SIGSTOP)) _exit(90);
        char path[80], buffer[4096];
        snprintf(path, sizeof(path), "/proc/%d/status", parent);
        int stopped = 0;
        for (int attempt = 0; attempt < 1000; attempt++) {
            int file = open(path, O_RDONLY);
            ssize_t count = native_read(file, buffer, sizeof(buffer) - 1);
            close(file);
            if (count > 0) {
                buffer[count] = 0;
                if (strstr(buffer, "State:\tT")) { stopped = 1; break; }
            }
            usleep(100);
        }
        off_t result = native_lseek(fd, offset, whence);
        int saved = errno;
        if (kill(parent, SIGCONT) || !stopped) _exit(91);
        const char message[] = "proc-status seek: real parent S -> T -> S\n";
        write(2, message, sizeof(message) - 1);
        errno = saved;
        return result;
    }
    return native_lseek(fd, offset, whence);
}
