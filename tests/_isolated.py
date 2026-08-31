# -*- coding: utf-8 -*-
"""测试隔离：把账号库指到临时文件，杜绝测试污染真实 accounts.json。

用法：
    with isolated_accounts():
        pool = AccountPool(Config())
        ...  # pool.save() 落到临时文件
"""
import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import account_pool


@contextlib.contextmanager
def isolated_accounts():
    tmp_dir = tempfile.mkdtemp(prefix="amp2_test_")
    tmp_path = os.path.join(tmp_dir, "accounts.json")
    orig = account_pool.accounts_path
    account_pool.accounts_path = lambda: tmp_path
    try:
        yield tmp_path
    finally:
        account_pool.accounts_path = orig
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass


def isolate_global() -> str:
    """进程级隔离：整个测试进程期间账号库都指向临时文件（不再恢复）。

    用于脚本式测试（无统一 with 包裹、池对象生命周期跨函数）。
    教训：with 块提前退出后，池对象的 save() 会写回真实 accounts.json，
    曾把生产账号库覆盖成测试数据。
    """
    tmp_dir = tempfile.mkdtemp(prefix="amp2_test_")
    tmp_path = os.path.join(tmp_dir, "accounts.json")
    account_pool.accounts_path = lambda: tmp_path
    return tmp_path
