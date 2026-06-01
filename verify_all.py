import sys, traceback
sys.path.insert(0, 'scripts')

results = {}

# 1. core.py
try:
    import core
    # 测试 AgentMonitorService 是否有 get_department_health
    from core import AgentMonitorService, TaskService, AgentCatalogService
    ts = TaskService()
    ac = AgentCatalogService()
    am = AgentMonitorService(ts, ac)
    assert hasattr(am, 'get_department_health'), "缺少 get_department_health"
    results['core.py'] = 'OK — AgentMonitorService.get_department_health 存在'
except Exception as e:
    results['core.py'] = f'FAIL: {e}'

# 2. server.py
try:
    import dashboard.server as srv
    handler = srv.CorpPilotAPI
    for method in ['handle_get_models', 'handle_post_models', 'handle_get_traffic',
                   'handle_export_traffic', 'handle_run_task', 'handle_departments']:
        assert hasattr(handler, method), f"缺少方法 {method}"
    results['server.py'] = f'OK — 所有 API 方法均存在'
except Exception as e:
    results['server.py'] = f'FAIL: {e}\n{traceback.format_exc()}'

# 3. runtime 模块
modules = {
    'llm_client': ['LLMClient', 'ModelConfig'],
    'model_router': ['ModelRouter'],
    'traffic_monitor': ['TrafficMonitor'],
    'message_bus': ['MessageBus'],
    'tools': ['ToolExecutor', 'TOOL_SCHEMAS'],
    'agent_loop': ['agent_loop'],
    'agent_manager': ['AgentManager'],
}
for mod, cls_list in modules.items():
    try:
        m = __import__(f'runtime.{mod}', fromlist=cls_list)
        for cls in cls_list:
            assert hasattr(m, cls), f"缺少 {cls}"
        results[f'runtime/{mod}.py'] = f'OK'
    except Exception as e:
        results[f'runtime/{mod}.py'] = f'FAIL: {e}'

# 4. ModelRouter 读配置
try:
    from runtime.model_router import ModelRouter
    r = ModelRouter()
    ceo_cfg = r.resolve('ceo')
    risk_cfg = r.resolve('risk_center')
    results['model_router.resolve'] = f'OK — ceo→{ceo_cfg.model}, risk_center→{risk_cfg.model}'
except Exception as e:
    results['model_router.resolve'] = f'FAIL: {e}'

# 5. manage_models.py 语法
try:
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('manage_models', 'scripts/manage_models.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    results['manage_models.py'] = 'OK'
except Exception as e:
    results['manage_models.py'] = f'FAIL: {e}'

# 6. run_team.py 语法
try:
    spec = importlib.util.spec_from_file_location('run_team', 'scripts/run_team.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    results['run_team.py'] = 'OK'
except SystemExit:
    results['run_team.py'] = 'OK (SystemExit expected from argparse)'
except Exception as e:
    results['run_team.py'] = f'FAIL: {e}'

# 7. dashboard.html 结构
try:
    html = open('dashboard/dashboard.html', encoding='utf-8').read()
    checks = {
        'nav gateway btn': "switchTab('gateway')" in html,
        'gateway view': 'id="gateway"' in html,
        'triggerRunTask fn': 'triggerRunTask' in html,
        'loadTrafficStats fn': 'loadTrafficStats' in html,
        'loadGateway fn': 'loadGateway' in html,
        'loadModels fn': 'loadModels' in html,
        'no duplicate </script>': html.count('</script>') == 1,
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        results['dashboard.html'] = f'WARN — 缺失: {failed}'
    else:
        results['dashboard.html'] = f'OK — 所有检查项通过'
except Exception as e:
    results['dashboard.html'] = f'FAIL: {e}'

# 打印结果
print('\n=== CorpPilot Runtime 检查报告 ===\n')
for k, v in results.items():
    status = 'PASS' if v.startswith('OK') else ('WARN' if v.startswith('WARN') else 'FAIL')
    icon = {'PASS':'[✓]','WARN':'[!]','FAIL':'[✗]'}[status]
    print(f'  {icon} {k}: {v}')
print()
all_ok = all(v.startswith('OK') for v in results.values())
print('总结:', '全部通过' if all_ok else '存在问题，请查看上方')
