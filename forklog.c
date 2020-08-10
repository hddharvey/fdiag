#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>

#ifndef FORKLOG_FD
#error Please define me.
#endif

static int (*real_fork)(void);
static pid_t (*real_wait)(int *);
static pid_t (*real_waitpid)(pid_t, int *, int);

static FILE *logPipe = NULL;

static void setup(void) {
    if (logPipe) {
        return; // Already set up
    }

    real_fork = dlsym(RTLD_NEXT, "fork");
    real_wait = dlsym(RTLD_NEXT, "wait");
    real_waitpid = dlsym(RTLD_NEXT, "waitpid");
    if (!real_fork || !real_wait || !real_waitpid) {
        perror("failed to load symbols");
        exit(1);
    }

    // I don't make any effort to ensure that writes to this pipe from all the
    // different processes are synchronized in any way. Apparently, write()ing
    // to a pipe is atomic as long as there is room in the pipe. We'll be fine.
    logPipe = fdopen(FORKLOG_FD, "w");
    if (!logPipe) {
        perror("forklog: failed to fdopen log pipe");
        exit(1);
    }
}

/*

    If we send the fork message in the parent, then we risk the child does
    something before the parent's fork message is sent.

    If we send the fork message in the child, then we risk the parent's order
    of forking is wrong (since the relative ordering of messages from different
    processes is undefined).

    We'll choose the former option and leave it to the parser script to figure
    it all out.
*/

int fork(void) {
    int e = errno;
    setup();
    errno = e;

    int result = real_fork();

    e = errno;
    if (result == -1) {
        // Okay, a call to fork failed, probably a good time to send SIGKILL
        // to every member of our process group. (Slightly less drastic then
        // kill(-1, SIGKILL).
        fprintf(logPipe, "%d failed fork\n", (int)getpid());
        fflush(logPipe);
        kill(0, SIGKILL);
    } else if (result != 0) {
        fprintf(logPipe, "%d forked %d\n", (int)getpid(), result);
        fflush(logPipe);
    }
    errno = e;

    return result;
}

static void log_wait(pid_t pid, int status, const char *eventName) {
    int e = errno;
    setup();

    fprintf(logPipe, "%d %s %d", (int)getpid(), eventName, pid);

    if (WIFEXITED(status)) {
        fprintf(logPipe, " exited %d", WEXITSTATUS(status));
    } else if (WIFSIGNALED(status)) {
        fprintf(logPipe, " signaled %d", WTERMSIG(status));
    }

    fputc('\n', logPipe);
    fflush(logPipe);
    errno = e;
}

pid_t wait(int *wstatus) {
    int status;
    pid_t result = real_wait(&status);
    if (result > 0) {
        log_wait(result, status, "reaped");
    }

    if (wstatus != NULL) {
        *wstatus = status;
    }
    return result;
}

pid_t waitpid(pid_t pid, int *wstatus, int flags) {
    int status;
    pid_t result = real_waitpid(pid, &status, flags);
    if (result > 0) {
        log_wait(result, status, pid > 0 ? "reapedid" : "reaped");
    }

    if (wstatus != NULL) {
        *wstatus = status;
    }
    return result;
}

/* What follows are my attempts to prevent descendant processes from leaving
 * the process group that forktest creates them in. Mainly for peace of mind
 * tbh - if they really wanted to, they could still leave the group.
 */

int setpgid(pid_t pid, pid_t pgid) {
    errno = EPERM;
    return -1;
}

pid_t setsid(void) {
    errno = EPERM;
    return -1;
}

int setpgrp() { // args can vary
    errno = EPERM;
    return -1;
}
