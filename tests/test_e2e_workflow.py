#!/usr/bin/env python3
"""
CorpPilot E2E Tests
端到端测试 - 任务流转全流程
"""

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from task_manager import (
    TaskStatus, TaskType, TaskPriority,
    create_task, update_task_status, get_task, list_tasks,
    VALID_TRANSITIONS, ensure_data_dir
)


class TestResult:
    """测试结果收集器"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def assert_true(self, condition, message):
        """断言为真"""
        if condition:
            self.passed += 1
            print(f"  ✅ {message}")
        else:
            self.failed += 1
            self.errors.append(message)
            print(f"  ❌ {message}")
    
    def assert_equal(self, actual, expected, message):
        """断言相等"""
        self.assert_true(actual == expected, f"{message}: {actual} == {expected}")
    
    def assert_in(self, item, container, message):
        """断言包含"""
        self.assert_true(item in container, f"{message}: {item} in {container}")
    
    def summary(self):
        """输出测试摘要"""
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"测试结果: {self.passed}/{total} 通过")
        if self.failed > 0:
            print(f"\n失败的测试:")
            for err in self.errors:
                print(f"  - {err}")
        print(f"{'='*50}")
        return self.failed == 0


def test_task_creation():
    """测试任务创建"""
    print("\n📋 测试任务创建...")
    result = TestResult()
    
    # 创建任务
    task = create_task(
        title="测试任务",
        task_type=TaskType.RD,
        priority=TaskPriority.P1,
        requester="测试用户",
        description="这是一个测试任务"
    )
    
    result.assert_true(task is not None, "任务创建成功")
    result.assert_true(task["task_id"].startswith("TASK-"), "任务ID格式正确")
    result.assert_equal(task["title"], "测试任务", "任务标题正确")
    result.assert_equal(task["type"], "RD", "任务类型正确")
    result.assert_equal(task["priority"], "P1", "任务优先级正确")
    result.assert_equal(task["status"], "pending", "初始状态正确")
    result.assert_true(len(task["history"]) > 0, "历史记录已创建")
    
    return result


def test_status_transitions():
    """测试状态转换"""
    print("\n🔄 测试状态转换...")
    result = TestResult()
    
    # 创建任务
    task = create_task(
        title="状态转换测试",
        task_type=TaskType.PD,
        priority=TaskPriority.P2,
        requester="测试用户"
    )
    task_id = task["task_id"]
    
    # 测试合法转换
    transitions = [
        ("pending", "classified"),
        ("classified", "planned"),
        ("planned", "reviewing"),
        ("reviewing", "approved"),
        ("approved", "dispatched"),
        ("dispatched", "executing"),
        ("executing", "review"),
        ("review", "completed")
    ]
    
    for from_status, to_status in transitions:
        current = get_task(task_id)
        result.assert_equal(current["status"], from_status, f"当前状态为 {from_status}")
        
        try:
            updated = update_task_status(task_id, TaskStatus(to_status), "test")
            result.assert_equal(updated["status"], to_status, f"状态转换成功: {from_status} -> {to_status}")
        except ValueError as e:
            result.failed += 1
            result.errors.append(f"状态转换失败: {from_status} -> {to_status}: {e}")
    
    return result


def test_invalid_transitions():
    """测试非法状态转换"""
    print("\n🚫 测试非法状态转换...")
    result = TestResult()
    
    # 创建任务
    task = create_task(
        title="非法转换测试",
        task_type=TaskType.DA,
        priority=TaskPriority.P3,
        requester="测试用户"
    )
    task_id = task["task_id"]
    
    # 测试非法转换: pending -> completed (跳过中间状态)
    try:
        update_task_status(task_id, TaskStatus.COMPLETED, "test")
        result.failed += 1
        result.errors.append("非法转换未被拦截: pending -> completed")
    except ValueError as e:
        result.passed += 1
        result.assert_in("非法状态转换", str(e), "错误信息正确")
    
    # 测试非法转换: pending -> executing
    try:
        update_task_status(task_id, TaskStatus.EXECUTING, "test")
        result.failed += 1
        result.errors.append("非法转换未被拦截: pending -> executing")
    except ValueError as e:
        result.passed += 1
        print(f"  ✅ 非法转换被正确拦截: pending -> executing")
    
    return result


def test_rejected_flow():
    """测试驳回流程"""
    print("\n🔙 测试驳回流程...")
    result = TestResult()
    
    # 创建任务并推进到 reviewing
    task = create_task(
        title="驳回测试",
        task_type=TaskType.LEGL,
        priority=TaskPriority.P1,
        requester="测试用户"
    )
    task_id = task["task_id"]
    
    update_task_status(task_id, TaskStatus.CLASSIFIED, "test")
    update_task_status(task_id, TaskStatus.PLANNED, "test")
    update_task_status(task_id, TaskStatus.REVIEWING, "test")
    
    # 驳回
    updated = update_task_status(task_id, TaskStatus.REJECTED, "compliance")
    result.assert_equal(updated["status"], "rejected", "状态变为 rejected")
    
    # 重新规划
    updated = update_task_status(task_id, TaskStatus.PLANNED, "strategy")
    result.assert_equal(updated["status"], "planned", "可重新进入规划")
    
    return result


def test_task_list():
    """测试任务列表"""
    print("\n📚 测试任务列表...")
    result = TestResult()
    
    # 创建多个任务
    for i in range(3):
        create_task(
            title=f"列表测试 {i+1}",
            task_type=TaskType.TECH,
            priority=TaskPriority.P2,
            requester="测试用户"
        )
    
    # 获取列表
    tasks = list_tasks()
    result.assert_true(len(tasks) >= 3, "任务列表包含新建的任务")
    
    # 按状态筛选
    pending_tasks = list_tasks(status=TaskStatus.PENDING)
    result.assert_true(len(pending_tasks) > 0, "可按状态筛选")
    
    return result


def test_history_tracking():
    """测试历史记录追踪"""
    print("\n📝 测试历史记录追踪...")
    result = TestResult()
    
    task = create_task(
        title="历史追踪测试",
        task_type=TaskType.INFR,
        priority=TaskPriority.P1,
        requester="测试用户"
    )
    task_id = task["task_id"]
    
    # 执行多次状态转换
    update_task_status(task_id, TaskStatus.CLASSIFIED, "secretary")
    update_task_status(task_id, TaskStatus.PLANNED, "strategy")
    update_task_status(task_id, TaskStatus.REVIEWING, "compliance")
    
    # 检查历史记录
    updated_task = get_task(task_id)
    history = updated_task["history"]
    
    result.assert_true(len(history) >= 5, "历史记录完整")  # created + 3 transitions
    result.assert_in("status_change", history[-1]["action"], "最后一条记录是状态变更")
    
    return result


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*50)
    print("🧪 CorpPilot E2E 测试套件")
    print("="*50)
    
    # 设置测试数据目录
    test_data_dir = Path(tempfile.mkdtemp())
    os.environ["CORPPILOT_DATA_DIR"] = str(test_data_dir)
    
    # 重新设置数据目录
    import task_manager
    task_manager.DATA_DIR = test_data_dir
    task_manager.TASKS_FILE = test_data_dir / "tasks.json"
    ensure_data_dir()
    
    all_results = []
    
    try:
        all_results.append(test_task_creation())
        all_results.append(test_status_transitions())
        all_results.append(test_invalid_transitions())
        all_results.append(test_rejected_flow())
        all_results.append(test_task_list())
        all_results.append(test_history_tracking())
        
        # 汇总结果
        total_passed = sum(r.passed for r in all_results)
        total_failed = sum(r.failed for r in all_results)
        total_errors = []
        for r in all_results:
            total_errors.extend(r.errors)
        
        print(f"\n{'='*50}")
        print(f"总测试结果: {total_passed}/{total_passed + total_failed} 通过")
        if total_failed > 0:
            print(f"\n所有失败的测试:")
            for err in total_errors:
                print(f"  - {err}")
        print(f"{'='*50}")
        
        return total_failed == 0
    
    finally:
        # 清理测试数据
        shutil.rmtree(test_data_dir, ignore_errors=True)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
