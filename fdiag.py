#!/usr/bin/python3

import sys
import re
import argparse
import os
import signal
import curses

INDENT = ' ' * 8
COMMENT_TOK = '###' # String used to mark comments in log file

# Characters used to mark the start of each event (i.e., the starts of the
# horizontal dashed lines that connect two lanes).
WAIT_ANY_CHAR = 'w' # wait() or waitpid() with pid <= 0.
WAIT_ID_CHAR = 'i' # waitpid() with pid > 0.
FORK_CHAR = '+'

# Default spacing used on the diagram.
LANE_WIDTH = 4
# Default Prefix to log messages forwarded to stdout.
LOG_PREFIX = '=== '

# The default SIGINT handler from before we started messing around
SIGINT_DEFAULT_HANDLER = signal.getsignal(signal.SIGINT)


class Colour:
    ENABLED = sys.stdout.isatty()

    RESET = '\033[0;0m' if ENABLED else ''
    BOLD = '\033[1m' if ENABLED else ''
    RED_BOLD = '\033[31;1m' if ENABLED else ''
    GREEN_BOLD = '\033[32;1m' if ENABLED else ''
    YELLOW_BOLD = '\033[33;1m' if ENABLED else ''
    BLACK_BOLD = '\033[30;1m' if ENABLED else ''
    RED = '\033[31m' if ENABLED else ''
    YELLOW = '\033[33m' if ENABLED else ''

    SIGNAL = RED_BOLD
    EXITED = GREEN_BOLD
    ORPHAN = YELLOW_BOLD
    REAPED = BLACK_BOLD
    UNKNOWN = YELLOW_BOLD

    _strip_colour_regex = re.compile(r"\033\[[;\d]*[A-Za-z]")

    @staticmethod
    def strip_colours(string):
        """Will strip ANSI colour sequences from the specified string."""
        return Colour._strip_colour_regex.sub("", string)

    @staticmethod
    def init_curses_colours():
        """Sets up curses colour pairs with the colours used by this class.
        Make sure to call this before calling Colour.get_curses_colour(). This
        function returns the next colour pair that is available for use.
        """
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_BLACK)
        return 5

    @staticmethod
    def get_curses_colour(string):
        """Checks if the specified string starts with a colour sequence from
        this class, and if so, it returns a tuple of ( curses_colour, colour )
        where `colour` is the colour sequence from the start of the string.
        If no colour sequence was found, then ( None, None ) is returned.
        """
        if string.startswith(Colour.BOLD):
            return curses.color_pair(0) | curses.A_BOLD, Colour.BOLD
        elif string.startswith(Colour.RED_BOLD):
            return curses.color_pair(1) | curses.A_BOLD, Colour.RED_BOLD
        elif string.startswith(Colour.GREEN_BOLD):
            return curses.color_pair(2) | curses.A_BOLD, Colour.GREEN_BOLD
        elif string.startswith(Colour.YELLOW_BOLD):
            return curses.color_pair(3) | curses.A_BOLD, Colour.YELLOW_BOLD
        elif string.startswith(Colour.BLACK_BOLD):
            return curses.color_pair(4) | curses.A_BOLD, Colour.BLACK_BOLD
        elif string.startswith(Colour.RED):
            return curses.color_pair(1), Colour.RED
        elif string.startswith(Colour.YELLOW):
            return curses.color_pair(3), Colour.YELLOW
        elif string.startswith(Colour.RESET):
            return curses.color_pair(0), Colour.RESET
        else:
            return None, None


def print_key():
    """Print out the colour/etc. key used to display the fork diagram."""
    print(Colour.BOLD + '~~~~~~~~~~~~~ KEY ~~~~~~~~~~~~~' + Colour.RESET)
    print(Colour.UNKNOWN + ' ? ' + Colour.RESET + ' ended, but not sure how')
    print(Colour.BOLD + 'i-- waited for specific child' + Colour.RESET)
    print(Colour.BOLD + 'w-- waited for any child' + Colour.RESET)
    print(Colour.EXITED + '--- reaped, exited' + Colour.RESET)
    print('(' + Colour.EXITED + 'x' + Colour.RESET + ') ' + Colour.EXITED
            + 'orphaned, exited' + Colour.RESET)
    print(Colour.SIGNAL + '~~~ reaped, signaled' + Colour.RESET)
    print('[' + Colour.SIGNAL + 'x' + Colour.RESET + '] ' + Colour.SIGNAL
            + 'orphaned, signaled' + Colour.RESET)
    print(Colour.BOLD + '~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~' + Colour.RESET)


class ParseError(Exception):
    def __init__(self, line_num, msg=''):
        super().__init__(msg)
        self._line_num = line_num

    def show(self):
        print('Line %d is not valid. %s' % (self._line_num, str(self)))


class Process:
    def __init__(self, pid: int, parent=None):
        self.pid = pid
        self.parent = parent
        self.exited = True
        self.term_value = None # Could become a signal value or exit status
        self.events = [] # E.g., forking, waiting on another process
        self.reaped_by_parent = False

    def debug_print(self, indent=0):
        for event in self.events:
            event.debug_print(indent + 1)

        if self.exited:
            print(INDENT * (indent + 1) + Colour.EXITED + 'exited '
                    + Colour.RESET + str(self.term_value))
        else:
            print(INDENT * (indent + 1) + Colour.SIGNAL + 'signaled '
                    + Colour.RESET + str(self.term_value))

        if self.term_value is None:
            print(INDENT * (indent + 1) + 'unknown' + Colour.RESET)
        elif not self.reaped_by_parent:
            print(INDENT * (indent + 1) + 'orphaned' + Colour.RESET)


class Event:
    def __init__(self, parent, child):
        self.parent = parent
        self.child = child

    def debug_print(self, indent=0, recurse=False):
        """Print out this event to stdout with the specified indentation level.
        The `recurse` parameter specifies whether we also print the events for
        the child process if it's a fork event.
        """
        pass


class ReapEvent(Event):
    def __init__(self, parent, child, waited_on_any):
        """waited_on_any specifies whether the parent was waiting for any pid
        to finish, or a specific PID.
        """
        super().__init__(parent, child)
        self.waited_on_any = waited_on_any

    def debug_print(self, indent=0, recurse=False):
        event_name = 'reaped ' if self.waited_on_any else 'reapedid '
        print(INDENT * indent + event_name + str(self.child.pid))


class ForkEvent(Event):
    def debug_print(self, indent=0, recurse=True):
        colour = (Colour.REAPED if self.child.reaped_by_parent
                else Colour.ORPHAN)
        print(INDENT * indent + colour + 'forked ' + Colour.RESET
                + str(self.child.pid))
        if recurse:
            self.child.debug_print(indent)


class LogParser:
    """Takes the log output of forktest and builds an internal representation
    out of it (a process tree, using the above classes).
    """
    def __init__(self, is_quiet, log_prefix, print_unresolved=False):
        # We use this dictionary to keep track of which processes are either
        # running or are zombies that have not yet been reaped.
        self._active_processes = dict() # key is pid, value is class Process
        self._line_num = 0
        self._leader_process = None # The process that started it all
        # We use this dictionary to keep track of processes which we have not
        # seen an initial fork message for yet. This will happen whenever the
        # child process sends a message before the parent sends the fork message
        # that created it.
        self._unresolved_processes = dict() # Key is pid, value is Process.
        # Logging options
        self._quiet = is_quiet
        self._log_prefix = log_prefix
        # If true, we'll print when we encounter an unresolved process
        self._print_unresolved = print_unresolved

    def _get_exited(self, token):
        """Figure out 'exited' (True/False) value from a string. If not valid
        then crap ourselves.
        """
        if token == 'exited':
            return True
        elif token == 'signaled':
            return False
        else:
            raise ParseError(self._line_num,
                    'Expected "exited" or "signaled".')

    def _make_int(self, string):
        """Either `string` is a valid integer (which we return) or we crap
        ourselves.
        """
        try:
            return int(string)
        except ValueError:
            raise ParseError(self._line_num,
                    '"%s" is not a valid integer.' % string)

    def _get_process(self, pid):
        """Either `pid` is a valid PID (and we return it as an integer) or we
        put it on the backburner to figure out later.
        """
        if pid not in self._active_processes:
            # We haven't seen the initial fork message yet, so put this one on
            # the backburner.
            if self._print_unresolved:
                print(Colour.YELLOW_BOLD
                        + "note: I'll be waiting to see %d's parent." % pid
                        + Colour.RESET)
            proc = Process(pid, None) # Don't know its parent yet
            self._unresolved_processes[pid] = proc
            self._active_processes[pid] = proc

        return self._active_processes[pid]

    def _is_current_child(self, child, parent):
        """Returns true if `child` is a child of `parent` that has not yet been
        reaped. False otherwise.
        """
        for event in reversed(parent.events):
            if event.child == child:
                if type(event) == ReapEvent:
                    return False
                if type(event) == ForkEvent:
                    return True
        return False

    def _process_ended(self, pid, exited, code: int):
        """Called when the leader or an orphaned process ends on its own (i.e.,
        it isn't reaped by one of the other descendent processes).
        """
        proc = self._get_process(pid)
        proc.term_value = code
        proc.exited = exited
        proc.reaped_by_parent = (proc.parent is None) # Leader is always reaped
        del self._active_processes[pid]

    def _forked(self, parent_id, child_id):
        """Update the process tree with a fork event."""
        parent = self._get_process(parent_id)
        if child_id in self._unresolved_processes:
            # Okay, we've found the fork message that created this child.
            if self._print_unresolved:
                print(Colour.YELLOW_BOLD
                        + 'note: The parent of %d has been found.' % child_id
                        + Colour.RESET)
            child = self._unresolved_processes[child_id]
            child.parent = parent
            del self._unresolved_processes[child_id]
        elif child_id in self._active_processes:
            raise ParseError(self._line_num, 'Cannot fork onto existing PID.')
        else:
            child = Process(child_id, parent)
            self._active_processes[child_id] = child

        parent.events.append(ForkEvent(parent, child))

    def _failed_fork(self, pid):
        """This will never happen!!!"""
        print(Colour.RED + '%d failed to fork' % pid + Colour.RESET)
        print("This doesn't look very productive...")
        # The testing process configures itself to receive SIGINT when once
        # its parent has died (which causes it to kill and reap everything).
        # So let's just SIGKILL ourselves so that we can wrap this all up.
        os.kill(os.getpid(), signal.SIGKILL)

    def _reaped(self, parent_id, child_id, exited, code: int, waited_on_any):
        """Update the process tree with a reap event."""
        parent = self._get_process(parent_id)
        child = self._get_process(child_id)
        if not self._is_current_child(child, parent):
            raise ParseError(self._line_num,
                    '"%s" is not a valid child of "%s" at this time.'
                    % (child_id, parent_id))
        child.term_value = code
        child.exited = exited
        child.reaped_by_parent = True
        parent.events.append(ReapEvent(parent, child, waited_on_any))
        del self._active_processes[child_id]

    def _parse_event(self, tokens):
        """Incorporate the event info from this line into the process tree."""
        if len(tokens) == 3:
            pid = self._make_int(tokens[0])
            if tokens[1] == 'forked':
                child_id = self._make_int(tokens[2])
                self._forked(pid, child_id)
            elif tokens[1] == 'failed' and tokens[2] == 'fork':
                self._failed_fork(pid)
            else:
                raise ParseError(self._line_num)
        elif len(tokens) == 4:
            pid = self._make_int(tokens[1])
            did_exit = self._get_exited(tokens[2])
            status = self._make_int(tokens[3])
            if tokens[0] == 'leader':
                if pid != self._leader_process.pid:
                    raise ParseError(self._line_num,
                            '"%s" is not the leader\'s PID.' % pid)
                self._process_ended(pid, did_exit, status)
            elif tokens[0] == 'orphan':
                self._process_ended(pid, did_exit, status)
            else:
                raise ParseError(self._line_num)
        elif len(tokens) == 5:
            parent = self._make_int(tokens[0])
            child = self._make_int(tokens[2])
            did_exit = self._get_exited(tokens[3])
            status = self._make_int(tokens[4])
            if tokens[1] == 'reaped':
                self._reaped(parent, child, did_exit, status, True)
            elif tokens[1] == 'reapedid':
                self._reaped(parent, child, did_exit, status, False)
            else:
                raise ParseError(self._line_num)
        else:
            raise ParseError(self._line_num)

    def parse_line(self, line):
        """Parse a single line from the log file and return root of the process
        tree. Will forward log messages to stdout with log_prefix unless quiet
        is True.
        """
        self._line_num += 1
        tokens = [tok.strip() for tok in line.split(' ')]
        if len(tokens) < 1:
            raise ParseError(self._line_num, 'Empty line')
        if tokens[0] == COMMENT_TOK:
            print(Colour.YELLOW + line.strip() + Colour.RESET)
            return self._leader_process # Skip this line

        if not self._quiet:
            print(Colour.YELLOW
                    + self._log_prefix + line + Colour.RESET, end='')

        if self._line_num == 1:
            if len(tokens) != 2 or tokens[0] != 'leader':
                raise ParseError(self._line_num, 'Expected: leader [pid].')
            pid = self._make_int(tokens[1])
            self._leader_process = Process(pid)
            self._active_processes[pid] = self._leader_process
            return self._leader_process

        self._parse_event(tokens)
        return self._leader_process

    def parse(self, input_file):
        """Parse all the lines from input_file. Forwards other parameters to
        self.parse_line().
        """
        for line in input_file:
            self.parse_line(line)
        return self._leader_process


class Node:
    """This class represents the point in a process's lifecycle that occurs on
    a particular line. Each line contains a bunch of lanes, which are occupied
    by these Nodes.
    """
    def __init__(self, process=None, prev_node=None, do_nothing=False):
        """If do_nothing==True, then we'll ignore any pending events for the
        specified process and leave the event for a future node. If do_nothing
        is True, then this node will become a section of a straight line on the
        fork diagram. If do_nothing==False, then this node will take on the next
        event on the process's path (the one after prev_node's).

        prev_node must have a next event whenever do_nothing=False (except when
        prev_node=None).

        You only have to specify `process` when prev_node is None.
        """
        if prev_node is None:
            self.process = process
        else:
            self.process = prev_node.process

        # Index of the pending event for this path (the one that we're supposed
        # to execute if do_nothing=False).
        pending = prev_node._next_event_index if prev_node is not None else 0

        if do_nothing:
            self._event_index = None # This node won't do any forking or reaping
            self._next_event_index = pending
        else:
            self._event_index = pending # This node has an event
            self._next_event_index = pending + 1 # Requires pending!=None

        if (self._event_index is not None
                and self._event_index >= len(self.process.events)):
            self._event_index = None

        if (self._next_event_index is not None
                and self._next_event_index >= len(self.process.events)):
            self._next_event_index = None

    def my_event(self):
        """Return the event that occurs at this Node (possibly None)."""
        return (None if self._event_index is None
                else self.process.events[self._event_index])

    def pending_event(self):
        """Return the next event along the path that is waiting to happen.
        (Possibly None if the process has nothing left to do.)
        """
        return (None if self._next_event_index is None
                else self.process.events[self._next_event_index])

    def orphaned(self):
        """Return true if the process has no more events at this point, and if
        it doesn't need to be reaped (so the next step is to terminate).
        """
        return (not self.process.reaped_by_parent
                and self._next_event_index is None)

    def debug_print(self, indent=0):
        print(INDENT * indent + 'my id: %d' % self.process.pid)
        print(INDENT * indent +
                'events pending = %r' % (self.pending_event() is not None))
        print(INDENT * indent + 'orphaned = %r' % self.orphaned())
        print(INDENT * indent + 'my event: ', end='')
        if self.my_event() is None:
            print('None')
        else:
            print('')
            self.my_event().debug_print(indent + 1, recurse=False)


class Line:
    """Keep track on what is going on in each line of the diagram that we are
    going to output. The diagram is organized vertically (time pointing down).
    The graph is organized into 'lanes' which can only be occupied by one
    process at a time. Lanes are indexed from 0 (left to right).
    """
    def __init__(self):
        self.nodes = []

    def ready_to_be_reaped(self, process):
        """Return True if the specified process is ready to be reaped on this
        line of the diagram (assuming it actually is supposed to be reaped).
        """
        for node in self.nodes:
            if node.process is process:
                return node.pending_event() is None
        return False # If it hasn't been created yet, then it isn't ready


class Path:
    """Pretty simple."""
    def __init__(self, start_line):
        self.start_line = start_line
        self.end_line = None
        self.lane = None


def link_char(event):
    """Return the character used to draw the horizontal line when connecting
    the parent and child of an event on the fork diagram.
    """
    return '~' if type(event) == ReapEvent and not event.child.exited else '-'


def start_char(event):
    """Return the character used to mark the start of the event (i.e., start
    to begin the left-hand side of the line).
    """
    if type(event) == ReapEvent:
        return WAIT_ANY_CHAR if event.waited_on_any else WAIT_ID_CHAR
    return FORK_CHAR


def term_str(process, max_len):
    """Return the string used to terminate a process's path on the fork diagram
    (e.g., it's exit status). Truncates the string to fit in max_len.
    """
    if process.term_value is not None:
        string = str(process.term_value)
    else:
        raise RuntimeError('Oopsie poopsie')
    if len(string) > max_len:
        print(Colour.RED
                + ('warning: Had to truncate "%s" to fit within lane width.\n'
                    % string)
                + 'Maybe consider increasing the lane width.'
                + Colour.RESET)
        string = string[0:max_len]
    return string


def colour_of(thing):
    """Take an event or process and figure out what colour we should draw with
    on the diagram. If a process, then it's the colour of the termination. If
    an event, then it's the colour of the connecting line AND the termination.
    """
    if type(thing) == Process:
        if thing.term_value is None:
            raise RuntimeError('unreachable (I wish)')
        return Colour.EXITED if thing.exited else Colour.SIGNAL
    if type(thing) == ReapEvent:
        if thing.child.term_value is None:
            raise RuntimeError('unreachable (apparently not)')
        return Colour.EXITED if thing.child.exited else Colour.SIGNAL
    return ''


class Diagram:
    """Contains functions to build the fork diagram and then draw it."""
    def __init__(self, leader):
        self._leader = leader
        # Keep track of the paths occupied by each process
        self._paths = dict() # Key is class Process, value is class Path
        # The first line of the diagram will start with the parent in lane 0
        self._paths[leader] = Path(0)
        first_node = Node(process=leader, do_nothing=True)
        first_line = Line()
        first_line.nodes.append(first_node)
        # Stores 'Line' objects which keep track of what we need to draw on
        # each line. We start with line 1 containing just the parent object.
        self._lines = [first_line]
        # Stores the 'width' of the diagram in lanes.
        self._lane_count = 0

    def _get_node(self, line_num, lane_num):
        """Find the node in the specified line & lane (possibly None)."""
        for node in self._lines[line_num].nodes:
            if self._paths[node.process].lane == lane_num:
                return node
        return None

    def _allocate_process_to_lane(self, lanes, process):
        """Figure out what lane `process` goes in, based on the contents of all
        the lanes so far (in `lanes`), then repeat for all of the process's
        children (i.e., recursive).
        """
        my_path = self._paths[process]
        # Try to find where we can plonk it down. We iterate in reverse order
        # since we want children to 'fall on top' (so they appear to the right)
        # This method of lane allocation basically works like a game of Tetris.
        collision = False
        for i, lane in reversed(list(enumerate(lanes))):
            # Check if `process` overlaps with anything in this lane
            for p in lane:
                p_path = self._paths[p]
                if (my_path.end_line >= p_path.start_line
                        and my_path.start_line <= p_path.end_line):
                    collision = True # Can't fit it in this lane :-(
                    break
            if collision:
                if i + 1 < len(lanes):
                    # We've landed on something, so put it in the above lane
                    lanes[i + 1].append(process)
                    my_path.lane = i + 1
                else:
                    # We've landed on something, but there's no lane above us,
                    # so we have to create a new lane for it.
                    lanes.append([])
                    lanes[-1].append(process)
                    my_path.lane = len(lanes) - 1
                break

        if not collision:
            # We didn't hit anything, which means we can fit it on the bottom
            lanes[0].append(process)
            my_path.lane = 0

        # Now plonk down each of the children. We have to iterate backwards to
        # get the correct behaviour, so that we can avoid overlapping lines.
        for event in reversed(process.events):
            if type(event) == ForkEvent:
                self._allocate_process_to_lane(lanes, event.child)

    def _build_line(self, prev_line, line_num):
        """Construct the next line of the fork diagram and return it. line_num
        should be the index of the current line that we are building. We build
        the line based on the state of the diagram on the previous line.
        """
        cur_line = Line()
        # There are horizontal lines drawn across the fork diagram between lanes
        # to indicate forking and reaping. We have to keep track of if we are
        # currently inside one of those lines so we don't draw two of them on
        # top of each other. If we're inside an event, then this points to the
        # process that the event will terminate on.
        event_end = None

        # Iterate over the previous line and check the next event that each
        # process from the last line is waiting to perform. If a process from
        # the previous line wants to fork, then do that. If a process from the
        # previous line wants to reap another process, then wait until that
        # process is finished, and then arrange for that to happen.
        for node in prev_line.nodes:
            event = node.pending_event()
            path = self._paths[node.process]

            # If the process has been orphaned or this is the parent and it's
            # nothing left to do, then we should end it on this line. We only
            # want to set the finishing line if it hasn't already finished.
            if ((node.process is self._leader and event is None)
                    or node.orphaned()) and path.end_line is None:
                path.end_line = line_num

            # If we've reached the end of a dashed line, then indicate that.
            if event_end is node.process:
                event_end = None

            # If this process has run out of events, the we'll only add it if
            # it's not ready to die (we could be waiting for it to be reaped).
            if event == None:
                if (path.end_line is None
                        or path.end_line >= line_num):
                    cur_line.nodes.append(Node(prev_node=node, do_nothing=True))
                # Otherwise let the process die.
                continue

            # If we're currently embedded inside an event, then put off the
            # currently pending event for a bit.
            if event_end is not None:
                cur_line.nodes.append(Node(prev_node=node, do_nothing=True))
                continue

            if type(event) == ForkEvent:
                if event.child in self._paths:
                    raise RuntimeError("Shouldn't happen")
                self._paths[event.child] = Path(line_num)
                cur_line.nodes.append(Node(prev_node=node))
                cur_line.nodes.append(Node(process=event.child,
                                           do_nothing=True))
            elif type(event) == ReapEvent:
                if not prev_line.ready_to_be_reaped(event.child):
                    cur_line.nodes.append(Node(prev_node=node,
                                               do_nothing=True))
                    continue
                self._paths[event.child].end_line = line_num
                cur_line.nodes.append(Node(prev_node=node))
                event_end = event.child

        if len(cur_line.nodes) == 0:
            return None # We're done - all processes are dead
        return cur_line

    def build(self):
        """Figure out what line and lane everything will be drawn on."""
        # Allocate all the events to particular lines
        while True:
            line = self._build_line(self._lines[-1], len(self._lines))
            if line is None:
                break
            self._lines.append(line)
        # Start the list of lanes off as a list containing one empty lane
        lanes = [[]]
        # This function will recursively allocate all processes in the tree
        # to a lane of the diagram, starting with the leader process.
        self._allocate_process_to_lane(lanes, self._leader)
        self._lane_count = len(lanes)

    def _draw_node_of_diagram(self, node, line_num, lane_width):
        """Render a node on a specified line of the diagram. Returns a tuple
        of ( line1, line2, backsteps ) where line1 and line2 are the lines of
        output produced for this node and backsteps specifies how many steps
        backwards we are taking into the previous lane to draw this node.
        """
        if self._paths[node.process].end_line == line_num:
            if node.process.term_value is None:
                line = Colour.UNKNOWN + '?' + Colour.RESET
                return line, ' ', 0
            elif not node.process.reaped_by_parent:
                line = (('(' if node.process.exited else '[')
                        + colour_of(node.process)
                        + term_str(node.process, max_len=lane_width - 1)
                        + Colour.RESET
                        + (')' if node.process.exited else ']'))
                return line, ' ', 1
            else:
                line = ((' ' if node.process.exited else '~')
                        + colour_of(node.process)
                        + term_str(node.process, max_len=lane_width)
                        + Colour.RESET)
                return line, ' ', 1
        else:
            return '|', '|', 0

    def _draw_line_of_diagram(self, line, line_num, lane_width):
        """Draw the line. `line_num` is the index of the line (from 0). Will
        return the line as a list of lines (this list will contain either one
        or two lines). The width of each lane is padded to `lane_width`.
        """
        # If we're currently in the middle of drawing a dashed line to another
        # lane for an event (e.g., forking or reaping), then we use this to
        # keep track of what that event currently is.
        cur_event = None
        # For every line in self._lines, we'll actually print two lines:
        output1 = ' ' # Line that will contain events and stuff
        output2 = ' ' # Extra line of continuation to space things out
        for lane_num in range(0, self._lane_count):
            node = self._get_node(line_num, lane_num)
             # Amount of backwards steps we need to take to draw line 1 for this
             # node (we need this to draw path terminations which extend back
             # into the previous lane).
            backsteps = 0

            if node is None:
                line1 = ' ' if cur_event is None else link_char(cur_event)
                line2 = ' '
            elif cur_event is not None and node.process is cur_event.child:
                # We've reached the end of a dashed line across lanes.
                term = (term_str(cur_event.child, max_len=lane_width)
                        if type(cur_event) == ReapEvent else '+')
                line1 = term + Colour.RESET
                line2 = (' ' if self._paths[node.process].end_line == line_num
                        else '|')
                cur_event = None
            elif node.my_event() is not None:
                # The start of a new dashed line across lanes.
                if cur_event is not None:
                    # Can't print out overlapping events. This shouldn't be
                    # possible based on how I build the diagram.
                    raise RuntimeError('Ummm.... this is impossible!??')
                line1 = colour_of(node.my_event()) + start_char(node.my_event())
                line2 = '|'
                cur_event = node.my_event()
            else:
                line1, line2, i = self._draw_node_of_diagram(node, line_num,
                        lane_width)
                backsteps += i
                if cur_event is not None:
                    line1 = Colour.RESET + line1 + colour_of(cur_event)

            if backsteps > 0:
                output1 = output1[:-backsteps] # Delete last `backsteps` chars
            padding1 = lane_width - len(Colour.strip_colours(line1)) + backsteps
            padding2 = lane_width - len(Colour.strip_colours(line2))
            padding_char = ' ' if cur_event is None else link_char(cur_event)
            output1 += line1 + (padding_char * padding1)
            output2 += line2 + (' ' * padding2)

        if output2.strip() != '':
            return [output1, output2]
        return [output1]

    def draw(self, lane_width):
        """Draw the fork diagram. Now that we've collected information about
        how to lay the diagram out (via the `build` method), we can just loop
        through and draw each line. Returns the entire diagram as a list of
        lines. lane_width specifies the amount of columns per each lane.
        """
        rendered_lines = []
        for i, line in enumerate(self._lines):
            # Each line of the diagram will correspond to 1 or 2 actual lines.
            rendered_lines += self._draw_line_of_diagram(line, i, lane_width)
        return rendered_lines

    def debug_print(self, indent=0):
        print(Colour.BOLD + 'LINES' + Colour.RESET)
        for i, line in enumerate(self._lines):
            print(INDENT * indent + 'Line %d' % i)
            for node in line.nodes:
                lane = self._paths[node.process].lane
                print(INDENT * (indent + 1) + 'Lane %d' % lane)
                node.debug_print(indent + 2)

    def print_paths(self, indent=0):
        print(Colour.BOLD + '~' * 18 + ' PATHS ' + '~' * 18 + Colour.RESET)
        for process, path in self._paths.items():
            print(INDENT * indent + 'pid=%d start_line=%d end_line=%d lane=%d'
                    % (process.pid, path.start_line, path.end_line, path.lane))
        print(Colour.BOLD + '~' * (18 + len(' PATHS ') + 18) + Colour.RESET)


class ScrollView:
    """Allows you to create a scrollable view with curses that will properly
    handle coloured text. You scroll with the arrow keys and press q to quit.
    """
    def __init__(self, lines):
        self._lines = lines

    def _draw_window(self, win, pad, x, y, msg_colour, resized=True):
        """Draw the diagram onto the window and refresh. `resized` should be
        True if the window was resized since last drawn, or if this is the
        first time it is being displayed. (x, y) specify the offsets into the
        pad which we display onto the screen.
        """
        height, width = win.getmaxyx()
        if resized:
            win.clear()
            win.addstr(height - 1, 0, "Press q to quit...", msg_colour)
            win.refresh()
        pad.refresh(y, x, 0, 0, height - 2, width - 1)

    def _build_pad(self):
        """Take the lines of the diagram and write them into a curses pad of the
        same size. Will convert ANSI colour sequences from Colour class into
        curses colours. Returns pad and its dimensions as (pad, rows, cols).
        """
        cols = max([len(Colour.strip_colours(line)) for line in self._lines])
        rows = len(self._lines)
        pad = curses.newpad(rows, cols + 1) # TODO why is +1 needed lmao
        current_colour = None

        for y in range(0, rows):
            i = 0 # Offset into the line string, including colour sequence
            x = 0 # Current x coordinate, excluding non-printable sequences
            line = self._lines[y]
            while i < len(line):
                curses_col, colour = Colour.get_curses_colour(line[i:])
                if colour is None:
                    if current_colour is None:
                        pad.addch(y, x, ord(line[i]))
                    else:
                        pad.addch(y, x, ord(line[i]), current_colour)
                    x += 1
                    i += 1
                    continue

                i += len(colour) # Skip the colour sequence
                if colour is Colour.RESET:
                    current_colour = None
                else:
                    current_colour = curses_col

        return pad, cols, rows

    def _run(self, win):
        """Internal version of self.run() that the curses wrapper calls."""
        curses.start_color()
        next_pair = Colour.init_curses_colours()
        curses.init_pair(next_pair, curses.COLOR_BLACK, curses.COLOR_WHITE)
        msg_colour = curses.color_pair(next_pair) | curses.A_BOLD

        curses.curs_set(0)
        pad, pad_width, pad_height = self._build_pad()
        pad.keypad(True)
        x, y = 0, 0

        self._draw_window(win, pad, x, y, msg_colour)
        while True:
            c = pad.getch()
            win_height, win_width = win.getmaxyx()
            if c == curses.KEY_UP:
                y = max(y - 1, 0)
            elif c == curses.KEY_DOWN:
                y = max(min(y + 1, pad_height - win_height + 1), 0)
            elif c == curses.KEY_LEFT:
                x = max(x - 1, 0)
            elif c == curses.KEY_RIGHT:
                x = max(min(x + 1, pad_width - win_width), 0)
            elif c == ord('q'):
                break
            elif c == curses.KEY_RESIZE:
                self._draw_window(win, pad, x, y, msg_colour)
            else:
                continue
            self._draw_window(win, pad, x, y, msg_colour, resized=False)

    def run(self):
        """Run the curses application, then exit when 'q' is pressed."""
        # The curses.wrapper function will ensure that all the initialisation
        # and clean-up is done for us.
        curses.wrapper(lambda win: self._run(win))


def run_test(prog_argv, is_quiet, log_prefix):
    """Runs the test directly and returns the parsed process tree. prog_argv
    should be the full program argv, including argv[0]. Won't forward log
    messages to stdout if is_quiet=True.
    """
    # We have to use pipe2 since the file descriptor created by os.pipe() is
    # non-inheritable by default, unlike the actual system call.
    (r, w) = os.pipe2(0) # Oh god I LOVE tuple return values.
    pid = os.fork()
    if pid == -1:
        raise RuntimeError('Failed to fork testing process.')
    elif pid == 0:
        try:
            os.close(r)
        except Exception as e:
            print('Failed to close pipe: %s' % str(e))
            sys.exit(1)

        args = ['forktest', str(w)] + prog_argv
        # Try execing from $PATH
        try:
            os.execvp('forktest', args)
        except Exception as e:
            pass
        # Try execing from the working directory
        try:
            os.execvp('./forktest', args)
        except Exception as e:
            print('Failed to exec testing process: %s' % str(e))
            sys.exit(1)
    else:
        os.close(w)

    try:
        input_file = os.fdopen(r, "r")
        def sigint_handler(signum, frame):
            os.kill(pid, signal.SIGINT) # Forward to the child
        signal.signal(signal.SIGINT, sigint_handler)
        # Won't return until EOF reached
        leader = LogParser(is_quiet, log_prefix).parse(input_file)
    finally:
        os.kill(pid, signal.SIGINT) # Tell child to wrap everything up
        os.waitpid(pid, 0)
        signal.signal(signal.SIGINT, SIGINT_DEFAULT_HANDLER)
    return leader


###############################################################################
# COMMAND LINE ARGUMENTS AND MAIN
# I may have gone a little overboard with all these command line options, but
# it's just so easy to add new ones with this nifty argparse package!!!
###############################################################################

parser = argparse.ArgumentParser(
        description='Generates the fork diagram produced by COMMAND.',
        usage='%(prog)s [OPTIONS...] COMMAND [ARGS...]\n',
        epilog='Sending SIGINT will cause SIGKILL to be sent to all '
            'descendant processes. This program may not be able to detect '
            'the ending state of processes whose parents were abruptly '
            'terminated (in some cases). If a process\'s ending state is '
            'not accounted for, then it will have been reaped by its '
            'parent.')
parser.add_argument('-k', '--key',
        dest='show_key',
        action='store_true',
        help='print the colour key')
parser.add_argument('-s', '--scroll-view',
        dest='scroll_view',
        action='store_true',
        help='display the diagram in a scrollable curses window')
parser.add_argument('-r', '--read-stdin',
        dest='from_stdin',
        action='store_true',
        help='read process tree log messages from stdin instead')
parser.add_argument('-e', '--every-line',
        dest='every_line',
        action='store_true',
        help='when reading from stdin, show diagram on EVERY line')
parser.add_argument('-i', '--show-internal',
        dest='show_internal',
        action='store_true',
        help='show internal representation of process tree')
parser.add_argument('-d', '--show-debug',
        dest='show_debug',
        action='store_true',
        help='show debug information about diagram layout')
parser.add_argument('-l', '--list-paths',
        dest='show_paths',
        action='store_true',
        help='list the coordinates and PID for each path')
parser.add_argument('-q', '--quiet',
        dest='quiet',
        action='store_true',
        help='suppress log messages about process tree events')
parser.add_argument('-p', '--prefix',
        dest='log_prefix',
        action='store',
        type=str,
        default=LOG_PREFIX,
        help='specify the log message prefix (default: "%s")'
        % LOG_PREFIX)
parser.add_argument('-w', '--lane-width',
        dest='lane_width',
        type=int,
        default=LANE_WIDTH,
        action='store',
        help=
        'width of each lane on the diagram (default: %d)' % LANE_WIDTH)
parser.add_argument('input',
        metavar='COMMAND [ARGS...]',
        action='store',
        nargs=argparse.REMAINDER,
        help='the program and its arguments')


def show_diagram(leader, my_name, args):
    """Helper function for main to render the diagram based on the process tree
    and the program args (a Namespace object returned by the ArgumentParser).
    Might exit. my_name should be the name of this program.
    """
    if args.show_key:
        print_key()

    if leader is None:
        print("%s: No process tree." % my_name)
        return

    if args.show_internal:
        print(Colour.BOLD + 'leader %d' % (leader.pid)
                + Colour.RESET)
        leader.debug_print()

    diagram = Diagram(leader)
    diagram.build()
    if args.show_debug:
        diagram.debug_print()
    if args.show_paths:
        diagram.print_paths()
    lines = diagram.draw(args.lane_width)

    if args.scroll_view:
        if not sys.stdout.isatty():
            print('%s: Scroll view requires stdout be connected to a terminal.'
                    % my_name)
            sys.exit(1)
        input(Colour.BOLD + 'Press ENTER to show diagram...' + Colour.RESET)
        ScrollView(lines).run()
    else:
        for line in lines:
            print(line)


if __name__ == '__main__':
    args = parser.parse_args(sys.argv[1:])
    my_name = parser.prog

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)

    if args.lane_width < 2:
        print('%s: The minimum lane width is 2.' % my_name)
        sys.exit(1)

    try:
        if len(args.input) > 0:
            if args.from_stdin:
                print('%s: Ignoring %s, reading from stdin instead...'
                        % (my_name, args.input[0]))
            else:
                # Read log information from program, then display diagram
                leader = run_test(args.input, args.quiet, args.log_prefix)
                show_diagram(leader, my_name, args)
                sys.exit(0)

        if args.from_stdin:
            # We want to print messages about unresolved processes when we're
            # in interactive mode so the user doesn't get confused when the
            # script doesn't reject what looks like an error.
            parser = LogParser(args.quiet, args.log_prefix,
                    print_unresolved=True)

            if args.every_line:
                for line in sys.stdin:
                    leader = parser.parse_line(line)
                    show_diagram(leader, my_name, args)
            else:
                leader = parser.parse(sys.stdin)
                show_diagram(leader, my_name, args)

            sys.exit(0)
    except ParseError as e:
        e.show()
        sys.exit(1)

    # If we didn't print any diagrams then still show the key (maybe they just
    # wanted to see the key and not make a diagram?)
    if args.show_key:
        print_key()
