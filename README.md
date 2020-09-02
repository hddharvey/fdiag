#fdiag
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
