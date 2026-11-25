if __name__ == '__main__':
    # 去指纹层入口：必须在 import PySide6 / 读命令行之前执行
    import start_rebrand
    start_rebrand.boot()

    from config import config
    from kl import OK
    import src.kl_updater as kl_updater

    ok = OK(config)
    kl_updater.start_background_check()
    ok.start()
