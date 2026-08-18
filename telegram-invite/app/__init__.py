__version__ = '2.5.7'


def _fix_mac_version():
    """Telethon падает при импорте, если macOS назвала версию одним числом.

    В telethon/crypto/libssl.py стоит `ver, major, *_ = release.split('.')`,
    и на «26» вместо «26.5.2» это ValueError. Ловится он только как OSError,
    поэтому падает весь импорт — а вместе с ним и программа, ещё до окна.
    У одних macOS сообщает полную версию, у других короткую или пустую,
    и заранее не угадать: у нас «26.5.2», а на соседнем Mac — «26».

    Чиним у себя: телетон обновлять ради этого нельзя (там та же строка),
    а дополнять версию до трёх частей безопасно. Если версия не читается
    вовсе, называемся современной macOS — тогда telethon берёт путь с
    версионными libssl, а он на нынешних системах и есть правильный.
    """
    import platform
    import sys

    if sys.platform != 'darwin':
        return
    original = platform.mac_ver

    def mac_ver():
        release, version_info, machine = original()
        parts = [part for part in str(release).split('.') if part.isdigit()]
        if not parts:
            parts = ['99']
        while len(parts) < 3:
            parts.append('0')
        return '.'.join(parts), version_info, machine

    platform.mac_ver = mac_ver


_fix_mac_version()
