"""DeepSeek Chat Completions transport; no credentials/raw responses in diagnostics."""
import json
from ai_config import ConfigError, get_settings
from reliability import describe_api_error


def completion(messages, settings=None, max_tokens=None):
    from openai import OpenAI
    settings = settings or get_settings()
    if not settings.api_key:
        raise ConfigError("尚未配置密钥，请运行本机“配置AI接口”。")
    client = OpenAI(api_key=settings.api_key,base_url=settings.base_url,
                    timeout=float(settings.timeout),max_retries=0)
    try:
        return client.chat.completions.create(model=settings.model,messages=messages,
            response_format={"type":"json_object"},max_tokens=max_tokens or settings.max_tokens,
            extra_body={"thinking":{"type":"disabled"}},stream=False)
    finally:
        close = getattr(client,"close",None)
        if callable(close):
            close()


def test_connection(settings=None):
    """One small paid request with fixed test text; never sends a contract."""
    try:
        settings = settings or get_settings()
        response = completion([
            {"role":"system","content":'Connectivity test. Return only JSON: {"ok": true}.'},
            {"role":"user","content":"接口连通性检查，不包含合同。"}],settings,max_tokens=64)
        choice = response.choices[0]
        if getattr(choice,"finish_reason",None) not in (None,"stop") or json.loads(choice.message.content) != {"ok":True}:
            return {"ok":False,"message":"接口已响应，但测试JSON不符合要求，请检查模型及服务状态。"}
        return {"ok":True,"message":f"DeepSeek连接成功，模型：{settings.model}。仅验证连接与JSON格式，不代表法律判断准确。"}
    except ConfigError as exc:
        return {"ok":False,"message":str(exc)}
    except Exception as exc:
        return {"ok":False,"message":"连接测试未通过："+describe_api_error(exc)}
