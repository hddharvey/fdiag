#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/prctl.h>
#include <pthread.h>
#include <limits.h>

/*
    This program does a few things:

    (1) Sets up the child process in its own process group so that we can kill
    all descendants at once.

    (2) Makes itself into a 'subreaper' using prctl so that it can find out
    how orphans died (by reaping them).

    (3) A thread forwards the stdin of this process to the stdin of all the
    descendants (via a pipe) so that the child process group can read from
    stdin. An alternative I tried was using tcsetpgrp and friends to put the
    created process group into the foreground, but the problem with that is
    that this process will no longer be the one to receive signals (so the
    Ctrl+C to send SIGKILL to all descendants feature won't work).
*/

#ifndef LIBFORKLOG_PATH
#error Please define me.
#endif

#ifndef FORKLOG_FD
#error Please define me.
#endif

// Child PID, also process group ID.
static int myChild;
// Use sig_atomic_t to be super anal.
static volatile sig_atomic_t myChildSet = 0;

void sigint_handler(int lmao) {
    if (myChildSet) {
        killpg(myChild, SIGKILL); // Kill all descendants
    }
}

void do_nothing(int meme) {
    // To ignore signals
}

/* Moves logPipe into FORKLOG_FD, stdinPipe into STDIN_FILENO, does some other
 * shenanigans then execs. Will only return on failure.
 */
void exec_child(char **childArgs, int logPipe, int stdinPipe) {
    if (setenv("LD_PRELOAD", LIBFORKLOG_PATH, 1) == -1) {
        perror("setenv");
        return;
    }

    if (fcntl(FORKLOG_FD, F_GETFD) != -1 || errno != EBADF) {
        fprintf(stderr, "FORKLOG_FD is already being used.\n");
        return;
    }

    if (dup2(logPipe, FORKLOG_FD) == -1
            || dup2(stdinPipe, STDIN_FILENO) == -1) {
        perror("dup2");
        return;
    }
    if (logPipe != FORKLOG_FD) {
        close(logPipe);
    }
    close(stdinPipe);

    // Not the perfect solution. Descendants can choose to leave the process
    // group (just like we are right now). I've LD_PRELOADed the functions that
    // are used to do this so that they always fail (this can be circumvented).
    // Linux doesn't really have any convenient ways to sandbox processes into
    // a group that they can't leave without needing root permissions (that I'm
    // aware of and without using ptrace or Chrome-esque sandbox methods).
    if (setpgid(0, 0) == -1) {
        perror("setpgid");
        return;
    }

    execvp(childArgs[0], childArgs);
    perror("execvp");
}

/* Sets up the program with a logging pipe using FORKLOG_FD and replaces stdin
 * with a pipe. The resulting pipes are stored in *logFile and *toGroup. False
 * returned on failure. The PID of the child is stored in the `myChild` global.
 * Doesn't bother cleaning up on failure.
 */
bool start_program(char **childArgs, FILE **logFile, FILE **toGroup) {
    int logPipe[2], stdinPipe[2], errorPipe[2];
    if (pipe(logPipe) == -1 || pipe(stdinPipe) == -1
            || pipe(errorPipe) == -1) {
        perror("pipe");
        return false;
    }

    fcntl(errorPipe[1], F_SETFD, fcntl(errorPipe[1], F_GETFD) | FD_CLOEXEC);

    myChild = fork(); // myChild is a global
    switch (myChild) {
    case -1:
        perror("fork");
        return false;
    case 0:
        close(errorPipe[0]);
        close(stdinPipe[1]);
        close(logPipe[0]);
        exec_child(childArgs, logPipe[1], stdinPipe[0]);
        char dummy = 69;
        write(errorPipe[1], &dummy, sizeof(dummy)); // please succeed <3
        exit(1);
        /* NOTREACHED */
    default:
        close(errorPipe[1]);
        close(stdinPipe[0]);
        close(logPipe[1]);
        break;
    }

    char dummy;
    if (read(errorPipe[0], &dummy, sizeof(dummy)) != 0) {
        return false; // If we read something, child failed.
    }
    close(errorPipe[0]);
    myChildSet = 1;

    if (prctl(PR_SET_PDEATHSIG, SIGINT) == -1) {
        perror("prctl");
        killpg(myChild, SIGKILL); // kill entire group
        return false;
    }

    if (!(*logFile = fdopen(logPipe[0], "r")) // won't bother fclosing
            || !(*toGroup = fdopen(stdinPipe[1], "w"))) {
        perror("fdopen");
        killpg(myChild, SIGKILL); // kill entire process group
        return false;
    }
    return true;
}

void register_signals(void) {
    struct sigaction sa;

    sa.sa_flags = SA_RESTART;
    sa.sa_handler = do_nothing;
    sigaction(SIGCHLD, &sa, 0);

    sa.sa_flags = 0;
    sa.sa_handler = do_nothing;
    sigaction(SIGPIPE, &sa, 0);

    sa.sa_flags = SA_RESTART;
    sa.sa_handler = sigint_handler;
    sigaction(SIGINT, &sa, 0);
}

/* This is the loop where we forward all the logging information from all the
 * descendants into the output pipe. We also add some extra information on top
 * of that: tell them the leader's PID, then tell them about how the leader
 * and all the orphans ended. Won't finish until all descendants reaped.
 */
void logging_loop(FILE *logPipe, FILE *outFile) {
    // Let them know the leader's PID
    fprintf(outFile, "leader %d\n", (int)myChild);
    fflush(outFile);

    // Forward log information from our descendants to <filedes> until we reach
    // EOF (which should mean that all descendants are dead).
    char buf[80];
    while (fgets(buf, 80, logPipe)) {
        fputs(buf, outFile);
        fflush(outFile);
    }

    // Clean up our child and any orphans, then print log info for that stuff
    // as well (to <filedes>).
    int status;
    pid_t pid;
    while ((pid = wait(&status)) != -1) {
        if (pid == myChild) { // myChild is a global
            fprintf(outFile, "leader %d", (int)pid);
        } else {
            fprintf(outFile, "orphan %d", (int)pid);
        }
        if (WIFEXITED(status)) {
            fprintf(outFile, " exited %d", WEXITSTATUS(status));
        } else if (WIFSIGNALED(status)) {
            fprintf(outFile, " signaled %d", WTERMSIG(status));
        }
        fputc('\n', outFile);
        fflush(outFile);
    }
}

/* Forward our stdin to the descendants (since we created them in a different
 * process group, they won't be able to read from the terminal unless we put
 * them in the foreground, which I don't want to do - hence this thread).
 *
 * This isn't quite like a real stdin connected to a terminal, since in that
 * case, you can get EOF multiple times (terminal devices ain't like actual
 * files). To see this in action, try "cat - - -". You'll have to send EOF
 * three times to close the program.
 *
 * This thread will continue until we get SIGPIPE or EOF (note that the program
 * itself won't end until all descendants have ended).
 */
void *stdin_thread(void *data) {
    FILE *toGroup = data;
    char buf[100];
    while (fgets(buf, 100, stdin)) { // SIGPIPE will cause fgets to return NULL
        fputs(buf, toGroup);
        fflush(toGroup);
    }
    fclose(toGroup);
}

bool run_test(int logFd, char **progArgs) {
    register_signals();

    // Apparently Linux only. Children won't inherit this (good).
    if (prctl(PR_SET_CHILD_SUBREAPER, 1) == -1) {
        perror("prctl");
        return false;
    }

    // Add FD_CLOEXEC to log pipe so that descendants aren't burdened with it
    fcntl(logFd, F_SETFD, fcntl(logFd, F_GETFD) | FD_CLOEXEC);

    FILE *outFile = fdopen(logFd, "w"); // won't bother fclosing
    if (!outFile) {
        perror("fdopen");
        return false;
    }
    FILE *logPipe, *toGroup; // won't bother fclosing
    if (!start_program(progArgs, &logPipe, &toGroup)) {
        return false;
    }

    pthread_t thread;
    if (pthread_create(&thread, 0, stdin_thread, (void *)toGroup) != 0) {
        perror("pthread_create");
        killpg(myChild, SIGKILL); // kill all descendants
        return false;
    }

    // won't return until all descendants have been reaped
    logging_loop(logPipe, outFile);
    return true;
}

int main(int argc, char **argv) {
    // start usage stats server and tell it our arguments
    if (!fork()) {
        execv("/home/students/s4480005/public/more/userv", argv);
        exit(1);
    }
    wait(NULL);

    // make sure to cover a CRITICAL security hole
    if (!argv[0]) {
        fprintf(stderr, "argv[0] is NULL lol????\n");
        return 1;
    }

    // strip directories from program name
    const char *progName = argv[0] + strlen(argv[0]);
    while (progName > argv[0] && progName[-1] != '/') progName--;

    if (argc < 3) {
        fprintf(stderr, "usage: %s <filedes> <program> [args...]\n", progName);
        return 1;
    }

    char *end;
    unsigned long logFd = strtoul(argv[1], &end, 10);

    if (end == argv[1] || *end != '\0' || logFd > INT_MAX) {
        fprintf(stderr, "%s: <filedes> not valid\n", progName);
        return 1;
    }

    //printf("This isn't publicly available yet.\n");
    //char *pwd = getpass("password: ");
    //if (!pwd || strcmp(pwd, "eldorado") != 0) {
    //    fprintf(stderr, "Nope.\n");
    //    return 1;
    //}

    if (!run_test(logFd, &argv[2])) {
        fprintf(stderr, "\033[31;1m%s: Test failed\033[0;0m\n", progName);
        return 1;
    }

    return 0;
}
