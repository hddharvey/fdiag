#include <unistd.h>
#include <sys/wait.h>
#include <stdlib.h>
#include <stdio.h>

#define SEGFAULT *((int *)0) = 69;

int main(int argc, char **argv) {
    /*fork();
    fork();
    fork();
    fork();
    fork();
    while (1) fork();
    fork();*/
    // do some random stuff involving forking and waiting
    if (!fork()) {
        exit(101);
    }
    pid_t pid = fork();
    if (pid == 0) {
        SEGFAULT
    }

    if (!fork()) {
        raise(SIGINT);
    }
    waitpid(pid, NULL, 0);
    fork();
    fork();
    pid_t child = fork();
    if (child == 0) {
        exit(69);
    }
    waitpid(child, NULL, 0);
    if (!fork()) {
        if (!fork()) {
            SEGFAULT
        } else {
            wait(NULL);
            // Just run some random command that will spawn subprocesses to
            // see if it works.
            execlp("bash", "bash", "-c", "ps -F | grep ps | cat", NULL);
        }
        wait(NULL);
    } else {
        exit(69);
    }
    wait(NULL);
    return 0;
}
