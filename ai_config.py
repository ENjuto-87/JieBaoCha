"""Backend-only DeepSeek settings. No I/O or SDK imports at module import time."""
from dataclasses import dataclass, field
import base64
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
MAX_AI_CHARACTERS = 30_000


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AiSettings:
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    timeout: int = 60
    max_tokens: int = 8192
    source: str = "未配置"
    base_url: str = BASE_URL

    def signature(self):
        # Internal comparison only. Never expose a key-derived digest via HTTP/reports.
        return hashlib.sha256(json.dumps([self.api_key,self.model,self.timeout,self.max_tokens,self.base_url]).encode()).hexdigest()


def config_path():
    return Path(os.environ.get("CONTRACT_REVIEW_AI_CONFIG", "") or Path(__file__).with_name(".ai-settings.json"))


def validate_model(model):
    if not isinstance(model,str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}",model):
        raise ConfigError("模型名称格式无效，请填写服务商提供的模型 ID。")
    return model


def _integer(value, lower, upper, name):
    try:
        if isinstance(value,bool):
            raise ValueError()
        number = int(value)
    except (TypeError,ValueError):
        raise ConfigError(f"{name}必须为整数。") from None
    if str(number) != str(value) or not lower <= number <= upper:
        raise ConfigError(f"{name}须为{lower}到{upper}之间的整数。")
    return number


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise ConfigError("加密配置仅支持当前Windows用户；其他系统请使用环境变量。")
    class Blob(ctypes.Structure):
        _fields_ = [("size",wintypes.DWORD),("data",ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32",use_last_error=True)
    kernel = ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok = operation(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok = operation(ctypes.byref(source),"Contract Review Demo",None,None,None,1,ctypes.byref(output))
    if not ok:
        raise ConfigError("无法加密或解密本机密钥，请使用保存配置的同一Windows用户重新配置。")
    try:
        return ctypes.string_at(output.data,output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data,wintypes.HLOCAL))


def get_settings(api_key_override=None):
    # An explicit env key isolates existing tests and overrides encrypted local config.
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    key = api_key_override if api_key_override is not None else env_key
    saved = {}
    source = "环境变量" if key else "未配置"
    try:
        exists = not key and config_path().exists()
    except OSError:
        raise ConfigError("无法读取本机AI配置，请检查文件权限或重新配置。") from None
    if exists:
        try:
            if config_path().stat().st_size > 32_000:
                raise ValueError()
            saved = json.loads(config_path().read_text(encoding="utf-8"))
            if saved.get("schema") != 1 or saved.get("provider") != "deepseek":
                raise ValueError()
            key = _dpapi(base64.b64decode(saved["encrypted_key"],validate=True),decrypt=True).decode("utf-8")
            source = "本机Windows加密配置"
        except ConfigError:
            raise
        except Exception:
            raise ConfigError("本机AI配置损坏或不可读取，请重新运行“配置AI接口”。") from None
    if not isinstance(key,str) or any(c.isspace() for c in key) or len(key) > 1024:
        raise ConfigError("API Key格式无效，请在本机配置窗口重新输入。")
    model = validate_model(os.environ.get("DEEPSEEK_MODEL", "").strip() or saved.get("model",DEFAULT_MODEL))
    timeout = _integer(os.environ.get("DEEPSEEK_TIMEOUT", "") or saved.get("timeout",60),10,180,"超时秒数")
    tokens = _integer(os.environ.get("DEEPSEEK_MAX_TOKENS", "") or saved.get("max_tokens",8192),256,16384,"输出上限")
    return AiSettings(key,model,timeout,tokens,source)


def public_status():
    try:
        settings = get_settings()
        return {"provider":"DeepSeek", "configured":bool(settings.api_key), "model":settings.model,
                "source":settings.source, "timeout":settings.timeout,"max_tokens":settings.max_tokens,
                "max_characters":MAX_AI_CHARACTERS,"sdk_available":importlib.util.find_spec("openai") is not None,
                "message":"已配置，尚需测试连接验证。" if settings.api_key else "尚未配置。请双击项目中的“配置AI接口.bat”，在本机输入密钥。"}
    except ConfigError as exc:
        return {"provider":"DeepSeek","configured":False,"model":DEFAULT_MODEL,"source":"配置无效",
                "timeout":60,"max_tokens":8192,"max_characters":MAX_AI_CHARACTERS,"sdk_available":False,"message":str(exc)}


def save_settings(key, model=DEFAULT_MODEL, timeout=60, max_tokens=8192):
    key = key.strip()
    if not key or any(c.isspace() for c in key) or len(key)>1024:
        raise ConfigError("密钥不能为空、含空白或超过1024字符。")
    model = validate_model(model)
    timeout = _integer(timeout,10,180,"超时秒数")
    max_tokens = _integer(max_tokens,256,16384,"输出上限")
    record = {"schema":1,"provider":"deepseek","revision":uuid4().hex,"model":model,"timeout":timeout,
              "max_tokens":max_tokens,"encrypted_key":base64.b64encode(_dpapi(key.encode())).decode("ascii")}
    path = config_path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,prefix=".ai-config-",delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record,stream,ensure_ascii=False,indent=2)
        os.replace(temporary,path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return path
