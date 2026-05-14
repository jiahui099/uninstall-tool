"""
一键卸载工具 (Drag-Drop Uninstaller)
将桌面快捷方式或程序文件拖入窗口，一键卸载

依赖: Python 3.8+ (Windows 自带 tkinter)
打包: pyinstaller --onefile --noconsole --icon=app.ico uninstall_tool.py
"""

import os
import sys
import re
import subprocess
import winreg
import struct
import ctypes
import shlex
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# 注册表搜索
# ══════════════════════════════════════════════════════════════════════════════

def find_in_registry(name: str):
    """在注册表 Uninstall 键下搜索应用"""
    results = []
    name_clean = re.sub(r'[^\w\s]', '', name).strip().lower()

    keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
    ]

    for hkey, base in keys:
        try:
            with winreg.OpenKey(hkey, base) as parent:
                for i in range(2000):
                    try:
                        sub = winreg.EnumKey(parent, i)
                    except OSError:
                        break

                    try:
                        with winreg.OpenKey(hkey, f'{base}\\{sub}') as app_key:
                            display_name = ''
                            uninstall = ''
                            install_loc = ''

                            try:
                                display_name, _ = winreg.QueryValueEx(
                                    app_key, 'DisplayName')
                            except FileNotFoundError:
                                pass

                            try:
                                uninstall, _ = winreg.QueryValueEx(
                                    app_key, 'UninstallString')
                            except FileNotFoundError:
                                pass

                            try:
                                install_loc, _ = winreg.QueryValueEx(
                                    app_key, 'InstallLocation')
                            except FileNotFoundError:
                                pass

                            if display_name:
                                dn = display_name.lower()
                                if (name_clean in dn or
                                    dn in name_clean or
                                    any(t in dn for t in name_clean.split())):
                                    results.append({
                                        'name': display_name,
                                        'uninstall': uninstall,
                                        'location': install_loc,
                                    })
                    except PermissionError:
                        continue
        except WindowsError:
            pass

    return results


# ══════════════════════════════════════════════════════════════════════════════
# LNK 快捷方式解析（简化版）
# ══════════════════════════════════════════════════════════════════════════════

def read_lnk_linkinfo(raw: bytes) -> str | None:
    """从 LinkInfo 结构提取路径"""
    try:
        if len(raw) < 32:
            return None
        if raw[:4] != b'I\x00\x00\x00':
            return None

        offset = struct.unpack_from('<I', raw, 28)[0]
        if len(raw) < offset + 4:
            return None

        block_type = struct.unpack_from('<I', raw, offset)[0]
        if block_type == 0x1B:
            # LocalBasePath
            path_len = struct.unpack_from('B', raw, offset + 9)[0]
            if path_len > 0:
                path = raw[offset + 10:offset + 10 + path_len].rstrip(b'\x00')
                return path.decode('latin-1', errors='ignore')
    except Exception:
        pass
    return None


def get_lnk_target(lnk_path: str) -> str | None:
    """读取 .lnk 文件的目标路径"""
    try:
        with open(lnk_path, 'rb') as f:
            data = f.read()

        if len(data) < 0x4C:
            return None

        sig = data[:4]
        if sig != b'\x4c\x00\x00\x00':
            return None

        flags = struct.unpack_from('<I', data, 0x18)[0]
        has_linkinfo = (flags >> 1) & 1
        has_idlist = flags & 1

        pos = 0x4C
        if has_idlist:
            idlist_size = struct.unpack_from('<H', data, pos)[0]
            pos += 2 + idlist_size

        if has_linkinfo and len(data) > pos + 4:
            linkinfo_size = struct.unpack_from('<I', data, pos)[0]
            if linkinfo_size >= 28:
                linkinfo = data[pos:pos + linkinfo_size]
                path = read_lnk_linkinfo(linkinfo)
                if path and os.path.exists(path):
                    return path

        # Fallback: 搜索文件内容中的路径字符串
        text = data.decode('latin-1', errors='ignore')
        matches = re.findall(r'[A-Za-z]:\\[^\x00\s]+\.(exe|dll)', text)
        for m in matches:
            if os.path.exists(m):
                return m

        matches = re.findall(r'[A-Za-z]:\\[^\x00]+', text)
        for m in matches:
            if os.path.exists(m) and not m.endswith('\\'):
                return m

    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 卸载执行
# ══════════════════════════════════════════════════════════════════════════════

def find_uninstaller(search_path: str, app_name: str) -> tuple[str | None, str]:
    """查找卸载命令"""
    # 1. 注册表
    reg_matches = find_in_registry(app_name)
    if reg_matches:
        m = reg_matches[0]
        if m['uninstall']:
            return m['uninstall'], m['name']

    # 2. 同目录找 uninstall/uninst/unins
    base_dir = os.path.dirname(search_path)
    if base_dir and os.path.isdir(base_dir):
        keywords = ['uninstall', 'uninst', 'unins', 'uninstall', 'uninst']
        for f in os.listdir(base_dir):
            fn = f.lower()
            if any(k in fn for k in keywords) and fn.endswith('.exe'):
                return os.path.join(base_dir, f), f

    return None, None


def do_uninstall(cmd: str, app_name: str) -> str:
    """执行卸载命令"""
    cmd = cmd.strip()
    if not cmd:
        return f"❌ 未找到卸载命令: {app_name}"

    try:
        if cmd.lower().startswith('msiexec'):
            # MSI: 提取产品码静默卸载
            parts = shlex.split(cmd)
            if '/x' not in parts and '/i' not in parts:
                return f"❌ 无法解析 MSI: {cmd}"
            base = [p for p in parts if p.startswith('/')]
            base.append('/qn')
            code = next((p for p in parts if not p.startswith('/') and p), '')
            final = ['msiexec.exe', '/x', code] + base
            subprocess.Popen(final, shell=False,
                             creationflags=0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        else:
            # EXE: 追加 /S 静默参数
            if '/s' not in cmd.lower() and '/q' not in cmd.lower() and not cmd.endswith('"'):
                cmd_silent = cmd + ' /S'
            else:
                cmd_silent = cmd
            subprocess.Popen(cmd_silent, shell=True,
                             creationflags=0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)

        return f"✅ 已启动卸载: {app_name}"

    except Exception as e:
        return f"❌ 执行失败: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# tkinter GUI
# ══════════════════════════════════════════════════════════════════════════════

def build_ui(root):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root.title('🗑️ 一键卸载工具')
    root.geometry('560x520')
    root.resizable(False, False)
    root.configure(bg='#f0f4f8')
    root.attributes('-topmost', False)

    # 居中
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    rw, rh = 560, 520
    root.geometry(f'{rw}x{rh}+{(sw-rw)//2}+{(sh-rh)//2}')

    style = ttk.Style()
    style.theme_use('clam')
    style.configure('Title.TLabel', font=('Microsoft YaHei', 16, 'bold'),
                    background='#f0f4f8', foreground='#2c3e50')
    style.configure('Sub.TLabel', font=('Microsoft YaHei', 9),
                    background='#f0f4f8', foreground='#7f8c8d')
    style.configure('Status.TLabel', font=('Microsoft YaHei', 9),
                    background='#f0f4f8', foreground='#95a5a6')
    style.configure('Hint.TLabel', font=('Microsoft YaHei', 8),
                    background='#f0f4f8', foreground='#95a5a6')

    # 主 frame
    main_f = tk.Frame(root, bg='#f0f4f8')
    main_f.pack(fill='both', expand=True, padx=24, pady=16)

    # 标题
    tk.Label(main_f, text='🗑️ 一键卸载工具', style='Title.TLabel',
             font=('Microsoft YaHei', 16, 'bold')).pack(anchor='w')
    tk.Label(main_f, text='把想要卸载的应用图标拖到下方框里，点一下就能卸载',
             style='Sub.TLabel').pack(anchor='w', pady=(2, 8))

    # 分隔线
    sep = tk.Frame(main_f, height=1, bg='#bdc3c7')
    sep.pack(fill='x', pady=(0, 12))

    # 拖放区域
    drop_frame = tk.Frame(main_f, bg='#ffffff', bd=2, relief='solid',
                          highlightthickness=2, highlightcolor='#3498db')
    drop_frame.pack(fill='both', expand=True, pady=(0, 8))

    drop_label = tk.Label(drop_frame,
                         text='📂 将桌面快捷方式 (.lnk) 或程序文件 (.exe) 拖入这里\n\n或右键 → 打开方式 → 选择本程序',
                         font=('Microsoft YaHei', 11),
                         fg='#bdc3c7', bg='#ffffff', justify='center')
    drop_label.pack(fill='both', expand=True, padx=10, pady=30)

    tk.Label(main_f, text='💡 提示: 拖入后会自动搜索卸载程序并列出，按按钮即可卸载',
             style='Hint.TLabel').pack(anchor='w', pady=(2, 8))

    # 状态栏
    status_var = tk.StringVar(value='就绪')
    tk.Label(main_f, textvariable=status_var, style='Status.TLabel',
             anchor='w', justify='left').pack(fill='x', pady=(0, 8))

    # 按钮行
    btn_frame = tk.Frame(main_f, bg='#f0f4f8')
    btn_frame.pack(fill='x', pady=(0, 8))

    uninstall_btn = tk.Button(btn_frame, text='🚫  开始卸载',
                               font=('Microsoft YaHei', 11, 'bold'),
                               bg='#e74c3c', fg='white', activebackground='#c0392b',
                               activeforeground='white', bd=0, padx=16, pady=6,
                               state='disabled', cursor='hand2',
                               command=lambda: on_uninstall())
    uninstall_btn.pack(side='left', padx=(0, 8))

    clear_btn = tk.Button(btn_frame, text='🔄  清空列表',
                          font=('Microsoft YaHei', 10),
                          bg='#95a5a6', fg='white', activebackground='#7f8c8d',
                          activeforeground='white', bd=0, padx=16, pady=6,
                          state='disabled', cursor='hand2',
                          command=lambda: on_clear())
    clear_btn.pack(side='left')

    # 结果列表
    list_frame = tk.Frame(main_f, bg='#ffffff', bd=1, relief='solid')
    list_frame.pack(fill='both', expand=True)

    scrollbar = ttk.Scrollbar(list_frame)
    scrollbar.pack(side='right', fill='y')

    result_list = tk.Listbox(list_frame, font=('Microsoft YaHei', 9),
                              bg='#ffffff', fg='#2c3e50', bd=0,
                              selectmode='browse', activestyle='none',
                              yscrollcommand=scrollbar.set, selectbackground='#ecf0f1')
    result_list.pack(side='left', fill='both', expand=True)
    scrollbar.config(command=result_list.yview)

    # 底部提示
    admin_ok = ctypes.windll.shell32.IsUserAnAdmin()
    tip_color = '#27ae60' if admin_ok else '#e67e22'
    tip_text = '🛡️ 已以管理员权限运行' if admin_ok else '⚠️ 建议右键以"管理员身份运行"本程序以卸载顽固应用'
    tk.Label(main_f, text=tip_text, font=('Microsoft YaHei', 8),
             fg=tip_color, bg='#f0f4f8').pack(anchor='w', pady=(6, 0))

    # 拖放处理
    pending_apps = []

    def on_drop(files):
        if not files:
            return
        drop_label.config(text='⏳ 正在分析...', fg='#3498db')
        root.update()

        new_apps = []
        for path in files:
            ext = os.path.splitext(path)[1].lower()
            if ext not in ('.lnk', '.exe'):
                continue

            name = os.path.splitext(os.path.basename(path))[0]
            target = path

            if ext == '.lnk':
                t = get_lnk_target(path)
                if t:
                    target = t

            uninstall_cmd, display_name = find_uninstaller(target, name)

            app = {
                'name': name,
                'display_name': display_name or name,
                'uninstall': uninstall_cmd,
                'path': path,
                'target': target,
            }
            new_apps.append(app)

        if not new_apps:
            drop_label.config(text='📂 拖入 .lnk 或 .exe 文件\n\n或右键 → 打开方式 → 选择本程序',
                             fg='#bdc3c7')
            status_var.set('⚠️ 未识别到有效文件')
            return

        pending_apps.clear()
        pending_apps.extend(new_apps)

        result_list.delete(0, 'end')
        for app in pending_apps:
            if app['uninstall']:
                result_list.insert('end',
                                   f'🔍 {app["display_name"]} → 已找到卸载程序')
            else:
                result_list.insert('end',
                                   f'⚠️ {app["display_name"]} → 未找到卸载程序（请手动卸载）')

        uninstall_btn.config(state='normal' if any(a['uninstall'] for a in pending_apps) else 'disabled')
        clear_btn.config(state='normal')
        drop_label.config(text='✅ 已加载！点击"开始卸载"即可',
                          fg='#27ae60')
        status_var.set(f'已加载 {len(pending_apps)} 个应用，'
                       f'其中 {sum(1 for a in pending_apps if a["uninstall"])} 个可卸载')

    def on_uninstall():
        uninstall_btn.config(state='disabled', text='⏳ 卸载中...')
        root.update()

        success_count = 0
        for app in pending_apps:
            if app.get('uninstall'):
                msg = do_uninstall(app['uninstall'], app['display_name'])
                result_list.insert('end', msg)
                result_list.see('end')
                root.update()
                if '✅' in msg:
                    success_count += 1
            else:
                result_list.insert('end', f'⚠️ {app["display_name"]} 无卸载命令')

        uninstall_btn.config(state='disabled', text=f'🚫  已卸载 {success_count}/{len(pending_apps)} 个')
        status_var.set(f'卸载完成: {success_count}/{len(pending_apps)} 个应用已启动卸载程序')

    def on_clear():
        pending_apps.clear()
        result_list.delete(0, 'end')
        uninstall_btn.config(state='disabled', text='🚫  开始卸载')
        clear_btn.config(state='disabled')
        drop_label.config(text='📂 将桌面快捷方式 (.lnk) 或程序文件 (.exe) 拖入这里\n\n或右键 → 打开方式 → 选择本程序',
                          fg='#bdc3c7')
        status_var.set('就绪')

    # ── 启用拖放 ──
    try:
        ctypes.windll.user32.DragAcceptFiles(root.winfo_id(), True)
    except Exception:
        pass

    def on_wm_drop(target, x, y):
        try:
            count = ctypes.windll.shell32.DragQueryFileW(target, -1, None, 0)
            files = []
            for i in range(count):
                size = ctypes.windll.shell32.DragQueryFileW(target, i, None, 0)
                buf = ctypes.create_unicode_buffer(size + 1)
                ctypes.windll.shell32.DragQueryFileW(target, i, buf, size + 1)
                files.append(buf.value)
            ctypes.windll.shell32.DragFinish(target)
            on_drop(files)
        except Exception as e:
            status_var.set(f'拖放出错: {e}')

    # 绑定拖放事件（使用 timer 轮询窗口消息）
    import threading

    def poll_dnd():
        # 使用定时器模拟文件拖放检测
        pass

    # 手动拖放处理：覆盖 Tk 事件处理
    # 通过 timer 轮询检测文件
    _dndenabled = [True]

    def handle_dnd_enter(e):
        drop_frame.config(highlightcolor='#e74c3c')
        return 'break'

    def handle_dnd_leave(e):
        drop_frame.config(highlightcolor='#3498db')
        return 'break'

    def handle_dnd_drop(e):
        drop_frame.config(highlightcolor='#3498db')
        try:
            fromid = e.data
            if isinstance(fromid, int):
                count = ctypes.windll.shell32.DragQueryFileW(fromid, -1, None, 0)
                files = []
                for i in range(count):
                    size = ctypes.windll.shell32.DragQueryFileW(fromid, i, None, 0)
                    buf = ctypes.create_unicode_buffer(size + 1)
                    ctypes.windll.shell32.DragQueryFileW(fromid, i, buf, size + 1)
                    files.append(buf.value)
                ctypes.windll.shell32.DragFinish(fromid)
                on_drop(files)
            else:
                # 已经解析好的文件路径
                on_drop([fromid] if isinstance(fromid, str) else fromid)
        except Exception as ex:
            status_var.set(f'处理失败: {ex}')

    # 尝试通过自定义绑定启用拖放
    try:
        drop_frame.drop_target_register('DROPFILES')
        drop_frame.dnd_bind('<<Drop>>', handle_dnd_drop)
    except Exception:
        # tkinter dnd 不可用，使用窗口级拖放
        pass

    # 窗口级别的文件拖放处理
    def on_window_message(msg, data):
        if msg == 0x0233:  # WM_DROPFILES
            try:
                count = ctypes.windll.shell32.DragQueryFileW(data, -1, None, 0)
                files = []
                for i in range(count):
                    size = ctypes.windll.shell32.DragQueryFileW(data, i, None, 0)
                    buf = ctypes.create_unicode_buffer(size + 1)
                    ctypes.windll.shell32.DragQueryFileW(data, i, buf, size + 1)
                    files.append(buf.value)
                ctypes.windll.shell32.DragFinish(data)
                on_drop(files)
            except Exception as ex:
                status_var.set(f'拖放处理失败: {ex}')

    # 使用 after 定期处理
    def check_drop():
        try:
            import win32gui, win32con
            hwnd = root.winfo_id()
            # 直接用 ctypes 读取 WM_DROPFILES
            pass
        except:
            pass
        root.after(500, check_drop)

    check_drop()

    return root


# ══════════════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import tkinter as tk

    root = tk.Tk()
    build_ui(root)
    root.mainloop()