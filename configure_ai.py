"""Interactive local setup; reads credentials with getpass, never echoes them."""
import argparse
import getpass
import json
from ai_config import ConfigError, DEFAULT_MODEL, public_status, save_settings


def main():
    parser = argparse.ArgumentParser(description="配置本机DeepSeek接口，不显示密钥")
    parser.add_argument("--status",action="store_true",help="仅查看无密钥状态，不联网")
    parser.add_argument("--test",action="store_true",help="发送一条固定测试消息，可能产生少量API费用")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(public_status(),ensure_ascii=False,indent=2))
        return 0
    if args.test:
        from ai_client import test_connection
        result = test_connection()
        print(result["message"])
        return 0 if result["ok"] else 1
    print("配置DeepSeek官方接口：https://api.deepseek.com")
    print("密钥仅保存为当前Windows用户可解密的本机文件，不进入网页或报告。")
    print("此步骤不联网；选择AI审查时会把确认文本发送给DeepSeek，并产生API费用。")
    try:
        # Fail rather than silently fall back to echoed input when no real terminal.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error",getpass.GetPassWarning)
            key = getpass.getpass("请粘贴API Key（输入不显示，回车确认；空白取消）：").strip()
        if not key:
            print("已取消，原配置保持不变。")
            return 0
        model = input(f"模型 ID [默认 {DEFAULT_MODEL}]：").strip() or DEFAULT_MODEL
        save_settings(key,model)
        print("已保存Windows加密配置。请在网页点击“刷新配置”，然后“测试连接”。")
        print("若使用PyCharm环境变量配置，环境变量优先于本机文件；修改环境变量后需重启服务。")
        return 0
    except (KeyboardInterrupt,EOFError):
        print("\n已取消，原配置保持不变。")
        return 0
    except (ConfigError,getpass.GetPassWarning) as exc:
        print(str(exc) if isinstance(exc,ConfigError) else "当前窗口不支持隐藏输入，请双击配置脚本或在PyCharm终端运行。")
        return 1
    except OSError:
        print("配置保存失败，请检查项目目录写入权限；原配置已保留。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
