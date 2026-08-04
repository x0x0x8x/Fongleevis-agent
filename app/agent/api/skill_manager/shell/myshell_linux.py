#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pty
import subprocess
import time
import threading
import queue
import uuid
import select
import termios
import fcntl
import struct

TAG_START = "__START__"
TAG_END = "__END__"
TAG_DUMMY = "__DUMMY__"
LOOP_READ_TIMEOUT = 2
LOOP_READ_TIMEOUT_MAX = 30
INTERACTIVE_COMMAND_ON = False
__ADMIN_ON__ = False
__DEBUG_ON__ = False

def clean_text(text: str) -> str:
    if not text:
        return text
    while True:
        start = text.find('\x1b]0;')
        if start == -1:
            break
        end = text.find('\x1b\\', start + 3)
        if end == -1:
            break
        text = text[:start] + text[end + 2:]
    while True:
        start = text.find('\x1b[')
        if start == -1:
            break
        end = start + 2
        while end < len(text):
            c = text[end]
            if c.isalpha():
                break
            end += 1
        text = text[:start] + text[end + 1:]
    return text

def remove_carriage_return(text: str) -> str:
    if not text:
        return text
    return text.replace('\r', '')

def get_dummy_str():
    return uuid.uuid4().hex

def printm(msg):
    global __DEBUG_ON__
    if __DEBUG_ON__:
        print(msg)

def clear_queue(q):
    while not q.empty():
        try:
            q.get_nowait()
        except:
            break

def set_pty_size(master_fd, rows=24, cols=80):
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

def pty_write_init(proc, master_fd):
    for cmd in [
        'export LANG=en_US.UTF-8\n',
        'export PS1=""\n',
        'set +v\n',
        f'echo __init__ && echo {TAG_END}\n'
    ]:
        os.write(master_fd, cmd.encode('utf-8'))
        time.sleep(0.05)
    buffer = b""
    while True:
        try:
            r, _, _ = select.select([master_fd], [], [], 0.5)
            if r:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                buffer += data
                if b"__init__" in buffer:
                    start_time = time.time()
                    while time.time() - start_time < 5:
                        r, _, _ = select.select([master_fd], [], [], 0.2)
                        if r:
                            data = os.read(master_fd, 4096)
                            if data:
                                buffer += data
                                if TAG_END.encode() in buffer:
                                    return
            else:
                break
        except OSError:
            break

class PtyShell:
    def __init__(self):
        self.firstInit = True
        self.isRerunning = False
        self.init()

    def __del__(self):
        self.drop()

    def init(self):
        if self.firstInit:
            self.shell_output_queue = queue.Queue()
            self.bg_readline_server_run = False
            self.droped = False

        self.master_fd, slave_fd = pty.openpty()
        set_pty_size(self.master_fd, rows=100, cols=200)

        self.proc = subprocess.Popen(
            ['/bin/bash', '--norc', '--noprofile'],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.STDOUT,
            close_fds=True,
            preexec_fn=os.setsid
        )
        os.close(slave_fd)

        time.sleep(0.5)
        pty_write_init(self.proc, self.master_fd)

        if self.firstInit:
            self.th = self.run_pty_readline_th()
            while not self.bg_readline_server_run:
                time.sleep(0.1)
        self.firstInit = False

    def run_pty_readline_th(self):
        def loop_readline():
            old_flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

            line_buffer = ""
            while True:
                try:
                    start = time.perf_counter()
                    r, _, _ = select.select([self.master_fd], [], [], 0.1)
                    if r:
                        data = os.read(self.master_fd, 4096).decode('utf-8', errors='replace')
                        if not data and self.proc.poll() is not None:
                            raise Exception("process terminated")
                        cost_ms = (time.perf_counter() - start) * 1000
                        print(f"read: {cost_ms:.2f} ms")
                        printm("pty.read: " + data)

                        line_buffer += data
                        while '\n' in line_buffer:
                            line, line_buffer = line_buffer.split('\n', 1)
                            if line:
                                resp = clean_text(line + '\n')
                                if resp:
                                    self.shell_output_queue.put(resp)
                                    printm("pushQ: " + resp)
                    else:
                        time.sleep(0.01)
                except Exception as e:
                    if self.droped:
                        printm(f"终端正常关闭")
                        return
                    else:
                        printm(f"⚠终端异常关闭: {e}")
                        printm(f"重启...")
                        self.isRerunning = True
                        self.init()
                        self.isRerunning = False
                        printm(f"已重启")
                        continue

        t = threading.Thread(target=loop_readline, daemon=True)
        t.start()
        printm("background readline server running...")
        self.bg_readline_server_run = True
        return t

    def drop(self):
        self.droped = True
        try:
            os.close(self.master_fd)
        except:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            time.sleep(0.2)
            self.proc.kill()

    def __read(self, last_cmd):
        err = 0
        full_cmd = last_cmd
        dummy_cmd = f"echo {get_dummy_str()} && echo {TAG_END}\n"
        buff = []
        timeout_cnt = 0
        while True:
            try:
                line = self.shell_output_queue.get(timeout=LOOP_READ_TIMEOUT)
                printm("popQ: " + line)
                if full_cmd in line:
                    continue
                line = remove_carriage_return(line)
                if TAG_END in line:
                    break
                buff.append(line)
            except queue.Empty:
                timeout_cnt += 1
                printm(f"timeout {LOOP_READ_TIMEOUT}s * {timeout_cnt}")
                if timeout_cnt >= 2:
                    os.write(self.master_fd, dummy_cmd.encode('utf-8'))
                    start = time.perf_counter()
                    while True:
                        try:
                            line = self.shell_output_queue.get(block=True, timeout=0.5)
                        except queue.Empty:
                            if (time.perf_counter() - start) * 1000 >= LOOP_READ_TIMEOUT_MAX:
                                break
                            continue
                        if dummy_cmd in line:
                            break
                    break

        resp = ""
        for line in buff:
            if full_cmd in line or dummy_cmd in line:
                continue
            elif TAG_END in line:
                continue
            elif TAG_START in line:
                continue
            elif TAG_DUMMY in line:
                continue
            resp += line
        if timeout_cnt > 0:
            resp += "shell command timeout... or triggered interactive commands!\n"
            err = -2
        return resp, err

    def __write(self, cmd):
        err = 0
        if not cmd or cmd.strip() == "":
            printm("⚠️  拦截空命令，不执行")
            return "", -1

        full_cmd = f"echo {TAG_START} && {cmd} && echo {TAG_END}\n"

        while self.isRerunning:
            time.sleep(0.1)

        try:
            os.write(self.master_fd, full_cmd.encode('utf-8'))
        except Exception as e:
            printm(f"⚠终端异常关闭: {e}")
            printm(f"重启...")
            self.init()
            try:
                os.write(self.master_fd, full_cmd.encode('utf-8'))
            except Exception as e:
                err = -1
                return "shell error", err
        return self.__read(last_cmd=full_cmd)

    def __multi_line_write(self, lines):
        resp = ""
        err = 0
        for cmd in lines:
            tmp, terr = self.__write(cmd)
            resp += tmp
            if err == 0:
                err = terr
        return resp, err

    def send(self, cmd):
        global __DEBUG_ON__
        if not cmd or cmd.strip() == "":
            printm("⚠️  拦截空命令，不执行")
            return "", -1
        lines = cmd.splitlines()

        if __ADMIN_ON__:
            if "/kill" in cmd:
                self.__kill()
                return "killed", 0
            elif "/debug_on" in cmd:
                __DEBUG_ON__ = True
                return "debug turn on", 0
            elif "/debug_off" in cmd:
                __DEBUG_ON__ = False
                return "debug turn off", 0

        start = time.perf_counter()
        clear_queue(self.shell_output_queue)
        if len(lines) == 1:
            resp = self.__write(cmd)
        else:
            resp = self.__multi_line_write(lines)
        cost_ms = (time.perf_counter() - start) * 1000
        print(f"耗时: {cost_ms:.2f} ms")
        return resp

    def __kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        os.close(self.master_fd)

if __name__ == "__main__":
    shell = PtyShell()
    while True:
        text = input(">:")
        resp, err = shell.send(text)
        if err < 0:
            print("[ERROR]")
        print(resp)