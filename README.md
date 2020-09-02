# fdiag
This is a nifty little script that generates the fork diagram the results from
running a certain program or command. Run the python script with no arguments
to see a description of its arguments.

The python script uses the program forktest and the library libforklog.so in
the background to assist it. forktest runs the command with the library linked
to it (via LD_PRELOAD). The library redefines some syscalls so that information
is sent via a pipe (established by forktest). All this info is retrieved by the
python script and used to generate a diagram.

When running on your system, you first need to set the path to the library in
the Makefile. You could just do this as ./libforklog.so if you wanted it to be
searched for in the working directory (not as robust though).

## Platforms
This will probably only work on Linux. I've specifically tested it on my laptop
(which runs Ubuntu) and a student server running CentOS for a course that I'm
tutoring. This will definitely NOT work on Microshaft Wangblows!!! (LOL!!!)

It probably won't work on Mac even though it's Unix-y since (as I've heard) it
doesn't support LD_PRELOAD anymore. There's probably other stuff that would
break on different platforms anyway.
