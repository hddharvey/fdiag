# Library path used by forktest.c when setting LD_PRELOAD
LIBFORKLOG_PATH=/home/henry/Documents/projs/fdiag/libforklog.so

# Standardized pipe FD used by child processes. I can't think of any other non-
# invasive ways to communicate with the library embedded into an arbtirary user
# process (apart from setting environment variables).
FORKLOG_FD=64

OUTPUTS = libforklog.so forktest a.out

.PHONY: all clean

all: $(OUTPUTS)

clean:
	rm -rf $(OUTPUTS)

libforklog.so: forklog.c
	# Need _GNU_SOURCE to access some features
	gcc $^ -o $@ -fPIC -shared -ldl \
	    -DFORKLOG_FD=$(FORKLOG_FD) \
	    -D_GNU_SOURCE

forktest: forktest.c
	gcc $^ -g -o $@ -pthread \
	    -DLIBFORKLOG_PATH=\"$(LIBFORKLOG_PATH)\" \
	    -DFORKLOG_FD=$(FORKLOG_FD)

a.out: example.c
	gcc $^ -o $@

