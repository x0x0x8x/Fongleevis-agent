from winpty import PtyProcess
import time
import threading
import queue
import uuid

TAG_START = "__START__"
TAG_END = "__END__"
TAG_DUMMY = "__DUMMY__"
LOOP_READ_TIMEOUT = 10    #2s
LOOP_READ_TIMEOUT_MAX = 30
INTERACTIVE_COMMAND_ON = False
__ADMIN_ON__ = False
__DEBUG_ON__ = False

SHELL_ANSI_END_FLAG_WIN = '\x1b\\'
SHELL_ANSI_START_FLAG_WIN = '\x1b]'

def clean_text(text: str) -> str:
    if not text:
        return text

    # ==================== 第一步：删除 窗口标题码 \x1b]0;...\x1b\ ====================
    while True:
        # 查找固定开头
        start = text.find('\x1b]0;')
        if start == -1:
            break
        # 查找对应结尾
        end = text.find('\x1b\\', start + 3)
        if end == -1:
            break
        # 删除从 start 到 结尾的完整一段
        text = text[:start] + text[end + 2:]

    # ==================== 第二步：删除 CSI 控制码 \x1b[...字母 ====================
    while True:
        # 查找固定开头 \x1b[
        start = text.find('\x1b[')
        if start == -1:
            break
        # 从开头往后找【第一个字母】作为结束
        end = start + 2
        while end < len(text):
            c = text[end]
            # 遇到字母立即停止（h / l / H / m / A 等）
            if c.isalpha():
                break
            end += 1
        # 删除这一段
        text = text[:start] + text[end + 1:]

    return text
def remove_carriage_return(text: str) -> str:
    """
    专门移除字符串中的所有回车符 \r
    适用于 PTY/CMD 终端输出，不会破坏任何有效数据
    """
    if not text:
        return text
    # 无差别全局移除所有 \r
    return text.replace('\r', '')
def pty_write_init(p, base_cmd):
    p.write('@echo off\r\n')
    p.write('chcp 65001\r\n')
    if base_cmd:
        p.write(f"{base_cmd}\r\n")
        while True:
            line = p.readline()
            if base_cmd in line:
                break
            time.sleep(0.1)
    p.write(f"echo __init__ & echo {TAG_END}\r\n")
    while True:
        line = p.readline()
        if "__init__" in line:
            break
        time.sleep(0.1)
    while True:
        line = p.readline()
        if TAG_END in line:
            break
        time.sleep(0.1)
    return
def get_dummy_str():
    return uuid.uuid4().hex
def printm(str):
    global __DEBUG_ON__
    if __DEBUG_ON__:
        print(str)
def clear_queue(q):
    while not q.empty():
        try:
            q.get_nowait()  # 拿一条，扔一条
        except:
            break
def replace_lf_to_crlf(text: str) -> str:
    """
    将字符串中【独立的 \n】（不是 \r\n 的一部分）替换为 \r\n
    已有的 \r\n 保持不变
    """
    result = []
    i = 0
    length = len(text)

    while i < length:
        # 如果当前是 \r，且下一个是 \n → 保留原样
        if text[i] == '\r' and i + 1 < length and text[i + 1] == '\n':
            result.append('\r\n')
            i += 2
        # 如果当前是独立的 \n → 替换成 \r\n
        elif text[i] == '\n':
            result.append('\r\n')
            i += 1
        # 普通字符直接保留
        else:
            result.append(text[i])
            i += 1

    return ''.join(result)

class PtyShell:
    def __init__(self, base_cmd=None):
        self.firstInit = True
        self.isRerunning = False
        #print("new shell, base cmd: ", base_cmd)
        self.init(base_cmd=base_cmd)

    def __del__(self):
        self.drop()

    def init(self, base_cmd):
        if self.firstInit:
            self.shell_output_queue = queue.Queue()
            self.bg_readline_server_run = False
            self.droped = False
            self.base_cmd = base_cmd
        self.pty = PtyProcess.spawn([
            "cmd.exe",
            "/q",  # 安静模式，无多余输出
            "/k",
            "mode con crt"  # 关键！关闭行缓冲，实时刷新输出
        ])
        time.sleep(0.5)
        pty_write_init(self.pty, self.base_cmd)

        if self.firstInit:
            self.th = self.run_pty_readline_th()
            while not self.bg_readline_server_run:
                time.sleep(0.1)
        self.firstInit = False
        pass
    def run_pty_readline_th(self):
        def loop_readline():
            while True:
                try:
                    start = time.perf_counter()
                    resp = self.pty.readline()
                    if not resp and not self.pty.isalive():
                        raise Exception("")
                    cost_ms = (time.perf_counter() - start) * 1000
                    #print(f"readline: {cost_ms:.2f} ms")
                    printm("pty.read: "+resp)
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
                if resp:
                    #resp2 = clean_text(resp)
                    resp = clean_text(resp)
                    if resp:
                        self.shell_output_queue.put(resp)
                        printm("pushQ: "+resp)

        t = threading.Thread(target=loop_readline, daemon=True)
        t.start()
        printm("background readline server running...")
        self.bg_readline_server_run = True
        return t
    def drop(self):
        self.droped = True
        self.pty.close()
        pass
    def __read(self, last_cmd):
        #buff.append("Do not trigger interactive commands!")
        err = 0
        full_cmd = last_cmd
        dummy_cmd = f"echo {get_dummy_str()} & echo {TAG_END}\r\n"
        buff = []
        timeout_cnt = 0
        while True:
            try:
                line = self.shell_output_queue.get(timeout=LOOP_READ_TIMEOUT)
                printm("popQ: "+line)
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
                    self.pty.write(dummy_cmd)
                    self.pty.sendeof()
                    start = time.perf_counter()
                    while True:
                        try:
                            line = self.shell_output_queue.get(block=True)
                        except queue.Empty:
                            continue
                        cost_ms = (time.perf_counter() - start) * 1000  # 转成毫秒
                        if cost_ms >= LOOP_READ_TIMEOUT_MAX:
                            break
                        if dummy_cmd in line:
                            break
                    break
            #time.sleep(0.2)

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
        full_cmd = f"echo {TAG_START} & {cmd} & echo {TAG_END}\r\n"

        while self.isRerunning:
            time.sleep(0.1)

        try:
            self.pty.write(full_cmd)
            pass
        except Exception as e:
            printm(f"⚠终端异常关闭: {e}")
            printm(f"重启...")
            self.init()

            try:
                self.pty.write(full_cmd)
            except Exception as e:
                err = -1
                return "shell error", err
            pass
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
        #print(f"耗时: {cost_ms:.2f} ms")

        return resp
    def __kill(self):
        self.pty.kill(sig=9)
if __name__ == "__main__":
    shell = PtyShell(base_cmd="cd d:\\")

    while True:
        text = input(">:")
        resp, err = shell.send(text)
        if err < 0:
            print("[ERROR]")
        print(resp)

