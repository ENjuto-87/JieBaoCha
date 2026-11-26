"""借款担保合同审查 Demo 试用版。"""

from pathlib import Path
import copy
import os
from queue import Empty, Queue
from threading import Thread, Event

from review_display import run_review_job
from report_export import build_snapshot, save_json, save_html, save_docx, suggested_filename
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from source_import import import_source, source_summary
from background_jobs import run_import_job, run_export_job


SAMPLE = """借款及保证合同
甲向乙借款人民币100000元。
借款期限自2026年1月1日至2026年12月31日。
丙为该笔借款提供保证。
保证方式：
保证期间：
送达地址：另行提供。
"""


def validate_text(text):
    """只判断是否能作为输入；不删除原文空格、换行。"""
    if not text.strip():
        raise ValueError("合同为空，请粘贴合同或导入有内容的 TXT 文件。")
    if "\x00" in text:
        raise ValueError("文本含有异常空字符，请检查编码或重新另存为 UTF-8 TXT。")
    return text


def read_txt(path, encoding):
    """读取用户选中的完整路径，不依赖 PyCharm 的工作目录。"""
    path = Path(path)
    if path.suffix.lower() != ".txt":
        raise ValueError("本课支持 TXT 文件；Word 内容可以先复制粘贴。")
    return validate_text(path.read_bytes().decode(encoding))


class ContractInputApp:
    def __init__(self, root, review_fn=None):
        self.root = root
        self.prepared_text = None
        self.last_result = None
        self.report_snapshot = None
        self.busy = False
        self.closed = False
        self.review_fn = review_fn
        self.result_queue = Queue()
        self.poll_id = None
        self.input_controls = []
        self.export_buttons = []
        self.export_queue = Queue()
        self.export_poll_id = None
        self.import_queue = Queue()
        self.import_poll_id = None
        self.import_cancel = Event()
        self.source_record = None
        self.prepared_source = None
        self.import_history = []
        self.ocr_import_fn = import_source
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.title("借款担保合同辅助审查")
        root.geometry("1100x900")
        root.minsize(860, 700)
        style = ttk.Style(root)
        style.configure("TButton", padding=(9, 5))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei", 11, "bold"))

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="借款担保合同辅助审查", font=("Microsoft YaHei", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="1 导入 → 2 核对并确认 → 3 审查 → 4 人工复核 → 5 导出", font=("Microsoft YaHei", 11)).pack(anchor="w", pady=(4, 6))
        ttk.Label(frame, text="保证与抵押规则可能不适用，法律解释须律师复核。流程完成和引用匹配均不代表合同安全。", wraplength=760).pack(anchor="w", pady=(0, 8))
        panes = ttk.Panedwindow(frame, orient="vertical")
        panes.pack(fill="both", expand=True)
        input_panel = ttk.LabelFrame(panes, text="1–2 合同输入与确认", padding=10)
        result_panel = ttk.LabelFrame(panes, text="3–5 审查结果与导出", padding=10)
        panes.add(input_panel, weight=1)
        panes.add(result_panel, weight=2)

        toolbar = ttk.Frame(input_panel)
        toolbar.pack(fill="x")
        self.add_button(toolbar, "载入练习样本", self.load_sample, side="left")
        self.add_button(toolbar, "导入 TXT", self.import_txt, side="left", padx=8)
        self.add_button(toolbar, "导入图片 / PDF", self.import_ocr, side="left", padx=(0, 8))
        ttk.Label(toolbar, text="文件编码：").pack(side="left")
        self.encoding = tk.StringVar(value="utf-8-sig")
        self.encoding_box = ttk.Combobox(toolbar, textvariable=self.encoding, values=("utf-8-sig", "gb18030"), state="readonly", width=12)
        self.encoding_box.pack(side="left")
        self.add_button(toolbar, "清空", self.clear, side="right")
        self.anonymized = tk.BooleanVar(value=False)
        self.anonymized_checkbox = ttk.Checkbutton(
            input_panel, text="我已对本次输入脱敏（报告提示身份类意见须核对原件）",
            variable=self.anonymized, command=self.on_mode_change)
        self.anonymized_checkbox.pack(anchor="w", pady=(6, 0))
        self.input_controls.append(self.anonymized_checkbox)
        self.partial_ack = tk.BooleanVar(value=False)
        self.partial_checkbox = ttk.Checkbutton(input_panel, text="来源不完整时：我已知悉失败页，仅审查当前已确认文本，稍后补充原件", variable=self.partial_ack, command=self.on_mode_change)
        self.partial_checkbox.pack(anchor="w", pady=(4, 0))
        self.input_controls.append(self.partial_checkbox)
        ttk.Label(input_panel, text="图片 / PDF 先提取到下方编辑区。请对照原件校正金额、日期、利率和姓名；引用匹配不证明 OCR 与原件一致。", wraplength=760).pack(anchor="w", pady=(4, 0))

        self.editor = ScrolledText(input_panel, height=9, wrap="word", font=("Microsoft YaHei", 11), undo=True)
        self.editor.pack(fill="both", expand=True, pady=10)
        self.editor.bind("<<Modified>>", self.on_edit)

        actions = ttk.Frame(input_panel)
        actions.pack(fill="x")
        self.add_button(actions, "确认输入并预览", self.prepare_input, side="left")
        self.use_ai = tk.BooleanVar(value=False)
        self.ai_checkbox = ttk.Checkbutton(actions, text="使用 AI（会发送合同文本并产生 API 费用）", variable=self.use_ai, command=self.on_mode_change)
        self.ai_checkbox.pack(side="left", padx=10)
        self.input_controls.append(self.ai_checkbox)
        self.review_button = ttk.Button(actions, text="开始审查", command=self.start_review, state="disabled")
        self.review_button.pack(side="right")
        self.progress = ttk.Progressbar(result_panel, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))
        self.status = tk.StringVar(value="等待合同输入。")
        ttk.Label(result_panel, textvariable=self.status, wraplength=760, font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=8)
        exports = ttk.Frame(result_panel)
        exports.pack(fill="x", pady=(0, 8))
        for title, kind in (("导出 Word", "docx"), ("保存结果 JSON", "json"), ("导出 HTML 报告", "html")):
            button = ttk.Button(exports, text=title, state="disabled",
                                command=lambda selected=kind: self.export_report(selected))
            button.pack(side="left", padx=(0, 8))
            self.export_buttons.append(button)
        ttk.Label(exports, text="导出包含本次原文，均使用同一快照。").pack(side="left")
        self.tabs = ttk.Notebook(result_panel)
        self.tabs.pack(fill="both", expand=True)
        self.preview = self.add_output_tab("已确认原文")
        self.local_output = self.add_output_tab("本地待核查")
        self.ai_output = self.add_output_tab("AI 意见与证据")
        self.message_output = self.add_output_tab("流程消息")
        source_panel = ttk.Frame(self.tabs)
        self.tabs.add(source_panel, text="来源与初始提取记录")
        source_toolbar = ttk.Frame(source_panel)
        source_toolbar.pack(fill="x")
        self.source_choice = ttk.Combobox(source_toolbar, state="readonly", width=55)
        self.source_choice.pack(side="left", fill="x", expand=True)
        self.source_choice.bind("<<ComboboxSelected>>", self.show_source_record)
        ttk.Button(source_toolbar, text="打开所选来源原件", command=self.open_source).pack(side="right")
        self.source_output = ScrolledText(source_panel, height=12, wrap="word", font=("Microsoft YaHei", 10), state="disabled")
        self.source_output.pack(fill="both", expand=True)
        self.cancel_import_button = ttk.Button(result_panel, text="取消导入（当前页完成后停止）", command=self.import_cancel.set)
        self.cancel_import_button.pack_forget()

    def add_button(self, parent, text, command, **pack_options):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(**pack_options)
        self.input_controls.append(button)
        return button

    def add_output_tab(self, title):
        panel = ttk.Frame(self.tabs)
        self.tabs.add(panel, text=title)
        output = ScrolledText(panel, height=14, wrap="word", font=("Microsoft YaHei", 10), state="disabled")
        output.pack(fill="both", expand=True)
        return output

    @staticmethod
    def set_output(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def clear_results(self):
        self.last_result = None
        self.report_snapshot = None
        for button in self.export_buttons:
            button.configure(state="disabled")
        for output in (self.local_output, self.ai_output, self.message_output):
            self.set_output(output, "")


    def set_preview(self, text):
        self.set_output(self.preview, text)

    def invalidate_input(self):
        self.prepared_text = None
        self.set_preview("")
        self.clear_results()
        self.review_button.configure(state="disabled")
        self.status.set("文本已改变，请重新确认输入。")

    def on_edit(self, event=None):
        if self.busy:
            return
        if self.editor.edit_modified():
            self.editor.edit_modified(False)
            self.invalidate_input()

    def replace_text(self, text, source_record=None):
        if self.busy:
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.source_record = copy.deepcopy(source_record)
        self.partial_ack.set(False)
        self.invalidate_input()

    def show_source_record(self, event=None):
        index = self.source_choice.current()
        if 0 <= index < len(self.import_history):
            self.set_output(self.source_output, source_summary(self.import_history[index]))

    def open_source(self):
        index = self.source_choice.current()
        if not 0 <= index < len(self.import_history):
            return
        path = Path(self.import_history[index]["file_path"])
        try:
            # Local file only; Windows opens the user's existing viewer.
            if not path.is_file():
                raise OSError
            os.startfile(str(path))
        except OSError:
            messagebox.showerror("无法打开来源", "来源文件已移动、无权限或没有关联的查看程序。请手动打开原件核对。", parent=self.root)

    def import_ocr(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="导入本地合同图片或 PDF", filetypes=[("合同图片和 PDF", "*.png *.jpg *.jpeg *.pdf"), ("PDF", "*.pdf"), ("图片", "*.png *.jpg *.jpeg")])
        if not path:
            return
        self.start_import(path)

    def start_import(self, path):
        if self.busy:
            return
        self.import_cancel.clear()
        self.set_busy(True)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=1, value=0)
        self.cancel_import_button.pack(anchor="w", pady=4)
        self.status.set("正在读取来源文件；识别不会上传合同。原有文本与结果暂时保留。")
        try:
            Thread(target=run_import_job, args=(self.ocr_import_fn, path, self.import_cancel, self.import_queue), daemon=True).start()
        except RuntimeError:
            self.finish_import()
            self.status.set("导入任务未启动，原有文本与结果仍保留。")
            return
        self.import_poll_id = self.root.after(100, self.poll_import)

    def finish_import(self):
        self.cancel_import_button.pack_forget()
        self.progress.configure(mode="indeterminate", value=0)
        self.set_busy(False)

    def poll_import(self):
        self.import_poll_id = None
        if self.closed:
            return
        # Bound each polling batch so a many-page job cannot starve Tk events.
        for _ in range(20):
            try:
                kind, payload = self.import_queue.get_nowait()
            except Empty:
                break
            if kind == "progress":
                done, total, message = payload
                self.progress.configure(maximum=max(1, total), value=done)
                self.status.set(message)
                continue
            self.finish_import()
            if self.import_cancel.is_set():
                self.status.set("导入已取消，原有文本与审查结果仍保留。")
                return
            if kind == "error":
                self.status.set("导入未完成，原有文本与审查结果仍保留。")
                messagebox.showerror("导入失败", payload, parent=self.root)
                return
            record = payload
            self.import_history.append(copy.deepcopy(record))
            self.source_choice.configure(values=[f"{i+1}. {r['file_name']} | {r['status']} | {r['imported_at']}" for i, r in enumerate(self.import_history)])
            self.source_choice.current(len(self.import_history) - 1)
            self.show_source_record()
            self.tabs.select(4)
            if record["status"] == "failed":
                self.status.set("所有页面均无可用文本，原有文本与结果仍保留。失败原因见来源记录。")
                messagebox.showwarning("识别无可用文本", "所有页面识别失败或为空，请查看来源记录。原有功能仍可使用。", parent=self.root)
                return
            self.replace_text(record["initial_text"], source_record=record)
            if record["status"] == "partial":
                self.status.set(f"来源不完整，失败页：{record['failed_pages']}。请补充并核对；须知悉不完整来源后才能确认。")
            else:
                self.status.set(f"已提取 {record['page_count']} 页，尚未人工核对。请在编辑区校正并确认后再审查。")
            return
        self.import_poll_id = self.root.after(100, self.poll_import)

    def load_sample(self):
        self.replace_text(SAMPLE)
        self.status.set("练习样本已载入，请确认输入；甲、乙、丙为脱敏代称。")

    def import_txt(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择合同 TXT 文件", filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        try:
            text = read_txt(path, self.encoding.get())
        except UnicodeDecodeError:
            messagebox.showerror("编码不匹配", "请选择正确的文件编码后重新导入。\n旧版中文 ANSI 文件可尝试 gb18030；也可在记事本另存为 UTF-8。", parent=self.root)
            return
        except (OSError, ValueError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        self.replace_text(text)
        self.status.set(f"已导入 {Path(path).name}，请检查文字并确认输入。")

    def clear(self):
        self.replace_text("")
        self.status.set("已清空，等待合同输入。")

    def prepare_input(self):
        if self.busy:
            return
        if self.source_record and self.source_record["status"] == "partial" and not self.partial_ack.get():
            messagebox.showwarning("来源不完整", "存在失败或空白页。请核对来源记录并补充文本；若暂仅审查当前文本，须勾选知悉不完整来源。", parent=self.root)
            return
        # end-1c 排除 Text 控件自动附加的最后一个换行。
        contract_text = self.editor.get("1.0", "end-1c")
        try:
            contract_text = validate_text(contract_text)
        except ValueError as exc:
            messagebox.showwarning("输入待补充", str(exc), parent=self.root)
            return
        self.editor.edit_modified(False)
        self.clear_results()
        self.prepared_text = contract_text
        self.prepared_source = copy.deepcopy(self.source_record)
        self.review_button.configure(state="normal")
        self.set_preview(contract_text)
        self.status.set(f"输入已确认，共 {len(contract_text)} 个字符（含空格和换行）。尚未执行审查。")

    def on_mode_change(self):
        self.clear_results()
        self.status.set("审查模式已改变；输入确认仍有效，请重新开始审查。" if self.prepared_text is not None else "等待合同输入。")

    def set_busy(self, busy):
        self.busy = busy
        self.editor.configure(state="disabled" if busy else "normal")
        self.encoding_box.configure(state="disabled" if busy else "readonly")
        for widget in self.input_controls:
            widget.configure(state="disabled" if busy else "normal")
        self.review_button.configure(state="disabled" if busy or self.prepared_text is None else "normal")
        for button in self.export_buttons:
            button.configure(state="disabled" if busy or self.report_snapshot is None else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def start_review(self):
        if self.busy:
            return  # 重复点击不会启动第二个请求。
        current = self.editor.get("1.0", "end-1c")
        if self.prepared_text is None or current != self.prepared_text:
            self.invalidate_input()
            messagebox.showwarning("请先确认输入", "请确认当前合同后再开始审查。", parent=self.root)
            return
        contract_text = self.prepared_text  # 本次审查的原文快照。
        use_ai = self.use_ai.get()  # 在主线程读取开关；GUI 不需要 --ai 参数。
        if getattr(self, "source_record", None):
            if self.source_record["status"] == "partial" and not self.partial_ack.get():
                messagebox.showwarning("来源不完整", "须先确认仅针对当前不完整文本审查。", parent=self.root)
                return
            self.prepared_source = copy.deepcopy(self.source_record)
            self.prepared_source["partial_acknowledged"] = bool(self.partial_ack.get())
        self.clear_results()
        self.set_busy(True)
        self.status.set("正在审查，请等待；本次合同与开关已锁定。")
        thread = Thread(target=run_review_job, args=(contract_text, use_ai, self.result_queue, self.review_fn), daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self.set_busy(False)
            self.status.set("审查未启动，请重新尝试。")
            return
        self.poll_id = self.root.after(100, self.poll_result)

    def poll_result(self):
        self.poll_id = None
        if self.closed:
            return
        try:
            kind, payload, displays = self.result_queue.get_nowait()
        except Empty:
            self.poll_id = self.root.after(100, self.poll_result)
            return
        self.set_busy(False)
        if kind == "error":
            self.status.set(f"审查或展示失败（{payload}），本次没有可展示的新结果。")
            self.set_output(self.message_output, f"异常类型：{payload}。\n请核对文件位置、解释器和第1课入口。窗口可以继续操作；不要分享 Key。")
            self.tabs.select(3)
            return
        self.last_result = payload
        if getattr(self, "prepared_source", None) and self.prepared_source["status"] == "partial":
            payload["messages"].append("来源不完整，存在失败或空白页；本次仅对用户确认的当前文本审查，不是完整合同审查。")
            from review_display import format_result
            displays = format_result(payload)
        summary, local_text, ai_text, message_text = displays
        if getattr(self, "prepared_source", None) and self.prepared_source["status"] == "partial":
            summary = "来源不完整 | " + summary
        self.status.set(summary)
        self.set_output(self.local_output, local_text)
        self.set_output(self.ai_output, ai_text)
        self.set_output(self.message_output, message_text)
        self.tabs.select(2 if payload["ai_findings"] else 1)
        try:
            self.report_snapshot = build_snapshot(
                self.prepared_text, payload, self.use_ai.get(), self.anonymized.get(),
                review_origin="custom_review_fn" if self.review_fn is not None else "review_service",
                source_record=getattr(self, "prepared_source", None))
        except (ValueError, TypeError, KeyError):
            self.status.set(summary + " | 本次结果不满足报告保存条件。")
            return
        for button in self.export_buttons:
            button.configure(state="normal")

    def export_report(self, kind):
        if self.busy or self.report_snapshot is None:
            return
        current = self.editor.get("1.0", "end-1c")
        if current != self.report_snapshot["contract_text"]:
            self.invalidate_input()
            messagebox.showwarning("原文已改变", "请重新确认并审查后导出。", parent=self.root)
            return
        suffix = {"json": ".json", "html": ".html", "docx": ".docx"}[kind]
        path = filedialog.asksaveasfilename(
            parent=self.root, title="选择保存位置（文件包含合同原文）",
            initialdir=str(Path(__file__).resolve().parent),
            initialfile=suggested_filename(self.report_snapshot, suffix),
            defaultextension=suffix, confirmoverwrite=True,
            filetypes=[({"json": "JSON 结果", "html": "HTML 报告", "docx": "Word 报告"}[kind], "*" + suffix)])
        if not path:
            return  # 取消不会清除结果，也不会重新调用 API。
        if Path(path).suffix.lower() != suffix:
            messagebox.showwarning("扩展名不匹配", f"请使用 {suffix} 扩展名重新保存。", parent=self.root)
            return
        writer = {"json": save_json, "html": save_html, "docx": save_docx}[kind]
        frozen = copy.deepcopy(self.report_snapshot)
        self.set_busy(True)
        self.status.set("正在导出报告，当前审查结果已保留。")
        try:
            Thread(target=run_export_job, args=(writer, frozen, path, self.export_queue), daemon=True).start()
        except RuntimeError:
            self.set_busy(False)
            messagebox.showerror("保存失败", "导出任务未能启动，当前结果仍保留。", parent=self.root)
            return
        self.export_poll_id = self.root.after(100, self.poll_export)

    def poll_export(self):
        self.export_poll_id = None
        if self.closed:
            return
        try:
            kind, payload = self.export_queue.get_nowait()
        except Empty:
            self.export_poll_id = self.root.after(100, self.poll_export)
            return
        self.set_busy(False)
        self.status.set("报告已保存，当前审查结果仍可继续复核和导出。" if kind == "ok" else "导出失败，当前审查结果仍保留，可重新导出。")
        if kind == "ok":
            messagebox.showinfo("已保存", f"已保存到：\n{payload}\n\n文件包含本次审查原文与意见。导出未调用 AI。", parent=self.root)
        else:
            messagebox.showerror("保存失败", payload + "\n当前结果仍保留；已有目标文件不会被失败的写入破坏。", parent=self.root)

    def close(self):
        self.closed = True
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if getattr(self, "export_poll_id", None) is not None:
            self.root.after_cancel(self.export_poll_id)
        if getattr(self, "import_poll_id", None) is not None:
            self.root.after_cancel(self.import_poll_id)
        if hasattr(self, "import_cancel"):
            self.import_cancel.set()
        self.root.destroy()
        # 不强制停止网络请求，也不从后台线程访问已销毁的窗口。



def main():
    root = tk.Tk()
    ContractInputApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
