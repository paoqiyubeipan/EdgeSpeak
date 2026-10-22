"""生成程序自带的词典数据到 dict\\（打包前运行一次；之后程序会自己在后台更新）。

用法：python build_dict.py                 下载全部最新数据并生成
      python build_dict.py jmdict wiktionary   只重新生成其中几份（unidic / jmdict / wiktionary / ecdict）
数据来源和许可见 dict_store.py 开头。
"""
import logging
import os
import sys

import dict_store

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    names = sys.argv[1:] or dict_store.COMPONENTS
    for name in names:
        info = dict_store.LATEST[name]()
        logging.info("%s：%s", dict_store.LABELS[name], info["version"])
        dict_store.install(name, info, dict_store.BUNDLED_ROOT)
    for name, entry in dict_store.read_manifest(dict_store.BUNDLED_ROOT).items():
        path = os.path.join(dict_store.BUNDLED_ROOT, entry["path"])
        size = (sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)
                if os.path.isdir(path) else os.path.getsize(path))
        print(f"{name:11} {entry['version']:40} {size / 1e6:8.1f} MB")
