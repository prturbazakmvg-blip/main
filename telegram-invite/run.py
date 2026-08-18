# -*- coding: utf-8 -*-
"""Точка входа. Она же используется при сборке в исполняемый файл."""
import multiprocessing

from app.main import main

if __name__ == '__main__':
    # Без этого onefile-сборка на Windows может уйти в бесконечный
    # перезапуск самой себя, если что-то поднимет процесс.
    multiprocessing.freeze_support()
    main()
