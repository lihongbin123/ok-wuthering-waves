if __name__ == '__main__':
    import start_rebrand
    start_rebrand.boot()

    from config import config
    from kl import OK

    config['debug'] = True
    ok = OK(config)
    ok.start()
