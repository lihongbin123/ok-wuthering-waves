if __name__ == '__main__':
    # 去指纹层入口：必须在 import PySide6 / 读命令行之前执行
    from pathlib import Path
    import json

    import start_rebrand
    start_rebrand.boot()

    from config import config
    from kl import OK
    import src.kl_updater as kl_updater
    import src.kl_update_compat as update_compat

    update_compat.install()

    # 窗口标题等位置的版本号跟随 app.json 的 current_version
    try:
        with open(Path(__file__).resolve().parent.parent / 'app.json', 'r', encoding='utf-8-sig') as fh:
            config['version'] = json.load(fh).get('current_version') or config.get('version')
    except Exception:
        pass

    ok = OK(config)
    kl_updater.start_background_check()
    ok.start()
