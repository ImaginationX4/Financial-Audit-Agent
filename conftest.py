# conftest.py
import sys
from pathlib import Path
import pytest

# 1. 确保项目根目录在 sys.path 中
# .parent 拿到的是 tests 目录
# .parent.parent 向上再退一级，才能精准拿到 financial-audit-agent 项目根目录
project_root = Path(__file__).parent.parent

# 建议使用 insert(0, ...) 而不是 append，确保项目自身的包优先级最高
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 2. (可选) 定义一个全局的 Fixture，比如测试数据的路径
@pytest.fixture
def sample_data_dir():
    """返回测试数据文件夹的路径，供所有测试文件使用"""
    # 既然 conftest.py 已经位于 tests 目录下，直接找同级的 data 文件夹即可
    return Path(__file__).parent / "data"