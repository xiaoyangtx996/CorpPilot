"""Conservative host admission, not process limits or a monetary budget."""
import ctypes
import os


def _sample_windows():
    if os.name != 'nt':
        raise OSError('Windows host metrics unavailable')

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
            (name, ctypes.c_uint64) for name in ('total_phys','available_phys','total_page','available_page',
                                               'total_virtual','available_virtual','available_extended')]

    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    function = ctypes.WinDLL('kernel32', use_last_error=True).GlobalMemoryStatusEx
    function.argtypes = [ctypes.POINTER(MemoryStatus)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(memory)) or not 0 <= memory.available_phys <= memory.total_phys or memory.total_phys == 0:
        raise OSError('Host memory sample invalid')
    cpus = os.cpu_count()
    if type(cpus) is not int or cpus < 1:
        raise OSError('Host CPU sample invalid')
    return {'available_memory_mb': memory.available_phys // (1024 * 1024), 'cpu_count': cpus}


def requirements(config):
    if config.get('backend', 'local') == 'docker':
        return {'memory_mb': config['docker_memory_mb'], 'cpus': config['docker_cpus']}
    return {'memory_mb': config.get('local_worker_memory_mb', 1024), 'cpus': config.get('local_worker_cpus', 1)}


def snapshot(config, reservations):
    result = {'enabled': config.get('resource_admission_enabled', False),
              'available_memory_mb': None, 'cpu_count': None,
              'reserved_memory_mb': sum(row['memory_mb'] for row in reservations.values()),
              'reserved_cpus': sum(row['cpus'] for row in reservations.values()),
              'message': '资源准入未启用，仍遵守现有并发限制'}
    try:
        sample = _sample_windows()
        if (type(sample['available_memory_mb']) is not int or sample['available_memory_mb'] < 0
                or type(sample['cpu_count']) is not int or sample['cpu_count'] < 1):
            raise ValueError('Invalid host sample')
        result.update(sample)
        if result['enabled']:
            result['message'] = '按实际可用内存与逻辑CPU保守准入；预约不代表本地进程硬限或费用预算'
    except Exception:
        result['message'] = ('无法核实宿主可用资源，暂停新任务' if result['enabled'] else
                             '无法核实宿主可用资源；资源准入未启用，仍遵守原并发限制')
    return result


def denial(current, config, request):
    if not current['enabled']:
        return ''
    if current['available_memory_mb'] is None or current['cpu_count'] is None:
        return '无法核实宿主可用资源'
    if current['reserved_cpus'] + request['cpus'] > current['cpu_count']:
        return '逻辑CPU预约不足，等待活动实例释放'
    if current['reserved_memory_mb'] + request['memory_mb'] + config.get('host_reserve_memory_mb', 1024) > current['available_memory_mb']:
        return '实际可用内存不足以覆盖宿主保留和实例预约'
    return ''
