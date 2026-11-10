"""MiniMax H3 backend with isolated headless ComfyUI runtime (DEV07)."""
import atexit, base64, copy, json, os, socket, subprocess, threading, time, urllib.request, urllib.error, urllib.parse, uuid, secrets, queue, sys
from pathlib import Path
from .hidden_subprocess import hidden_windows_kwargs
from .windows_job import WindowsKillJob

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'comfy_bridge.json'
TEMPLATE = ROOT / 'comfy_h3_api_workflow.json'  # backwards-compatible original
REF_TEMPLATE = ROOT / 'comfy_h3_ref2va_api_workflow.json'
FL_TEMPLATE = ROOT / 'comfy_h3_fl2va_api_workflow.json'
_lock = threading.RLock()
_proc = None
_log = None
_logpath = None
_active_prompt_id = None
_watchdog = None
_worker_job = None
_worker_job_listener_pid = None
_session_epoch = 0
_session_active = False
def _configured_port():
    """Use a separate loopback port for each installed Forge copy.

    Default 8191 preserves old installs. Read only at Forge startup.
    """
    try:
        settings = json.loads(CONFIG.read_text(encoding='utf-8'))
        value = settings.get('embedded_port', 8191)
        port = int(value)
        if not 1024 <= port <= 65535:
            raise ValueError('port must be in 1024..65535')
        return port
    except (OSError, json.JSONDecodeError):
        return 8191
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f'Invalid embedded_port in {CONFIG}: {exc}') from exc


PORT = _configured_port()


def _user_selected_port():
    """An explicit embedded_port is a user choice; never override it."""
    try:
        return 'embedded_port' in json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        # Invalid config is already reported by _configured_port at import.
        return True


def _port_available(port):
    """Check if the loopback TCP port can be bound (no foreign server contact)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', int(port)))
            return True
    except OSError:
        return False


def _auto_select_worker_port():
    """Select a free port only when no owned worker is active.

    A second Forge/Comfy installation may already use the default 8191.
    Never kill, connect to or submit jobs to that unrelated worker.
    The value stays fixed until our own worker is shut down.
    """
    global PORT
    if _user_selected_port() or (_proc is not None and _proc.poll() is None):
        return
    if _port_available(PORT):
        return
    requested = PORT
    for candidate in range(8191, 8256):
        if candidate != PORT and _port_available(candidate):
            PORT = candidate
            print(f'[Comfy Neo] Port {requested} is occupied; automatically selected '
                  f'free local port {PORT}. Other ComfyUI processes were left untouched.', flush=True)
            return
    raise RuntimeError('Comfy Neo cannot start: no free local TCP port in 8191-8255. '
                       'Stop a server or configure embedded_port in comfy_bridge.json.')
_api_owner_verified_at = 0.0
_api_owner_verified_pid = None
_owner_logged_pair = None  # DEV07: log each verified worker/listener pair once




def _kill_process_tree(pid):
    if not pid:
        return
    try:
        if os.name == 'nt':
            flags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            subprocess.run(['taskkill','/PID',str(int(pid)),'/T','/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=10)
        else:
            try:
                os.killpg(os.getpgid(int(pid)), 15)
            except Exception:
                os.kill(int(pid), 15)
    except Exception:
        pass

def _parse_netstat_listener_pid(output, port):
    """Return the TCP LISTENING PID for *exactly* this local port.

    Avoid substring matches that confuse 8191 with 18191 or a remote port.
    """
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != 'TCP' or parts[-2].upper() != 'LISTENING':
            continue
        try:
            local_port = int(parts[1].rsplit(':', 1)[-1])
            owner = int(parts[-1])
        except (ValueError, IndexError):
            continue
        if local_port == int(port):
            return owner
    return None


def _listener_pid_windows(port):
    if os.name != 'nt':
        return None
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        output = subprocess.check_output(
            ['netstat', '-ano', '-p', 'tcp'], text=True, errors='replace',
            creationflags=flags,
        )
        return _parse_netstat_listener_pid(output, port)
    except Exception:
        return None


def _listener_belongs_to_worker(owner_pid):
    """Accept the worker PID OR one of its descendants, never arbitrary listeners.

    Windows venv/Scripts/python.exe may be a redirector: Popen returns the
    launcher PID, while the Python interpreter that owns port 8191 is a child.
    Use psutil's process ancestry rather than accepting any localhost ComfyUI.
    """
    proc = _proc
    if proc is None or proc.poll() is not None or owner_pid is None:
        return False
    if int(owner_pid) == int(proc.pid):
        return True
    try:
        import psutil
        parent = psutil.Process(int(proc.pid))
        return any(child.pid == int(owner_pid) for child in parent.children(recursive=True))
    except Exception as exc:
        print(f'[MiniMax H3 Embedded] cannot verify worker process tree: {exc}')
        return False


def _assert_owned_listener(owner_pid):
    """Verify ownership; print only once per launcher/listener pair."""
    global _owner_logged_pair
    if _listener_belongs_to_worker(owner_pid):
        global _worker_job_listener_pid
        proc = _proc
        # Windows venv launchers may spawn the real Python interpreter before
        # Popen returns and we can attach the launcher to the kill job. After
        # verifying ancestry, enrol the actual TCP listener as well.
        if _worker_job is not None and proc is not None and int(owner_pid) != int(proc.pid):
            if _worker_job_listener_pid != int(owner_pid):
                try:
                    _worker_job.assign_pid(owner_pid)
                    _worker_job_listener_pid = int(owner_pid)
                    print(f'[Comfy Neo] private listener PID {owner_pid} enrolled in Windows cleanup job')
                except Exception as exc:
                    print(f'[Comfy Neo] warning: listener job enrollment failed: {exc}')
        proc = _proc
        if proc is not None and int(owner_pid) != int(proc.pid):
            pair = (int(proc.pid), int(owner_pid))
            with _lock:
                if _owner_logged_pair != pair:
                    _owner_logged_pair = pair
                    print(f'[MiniMax H3 Embedded] verified owned child listener PID '
                          f'{owner_pid} (launcher PID {proc.pid})')
        return True
    raise RuntimeError(
        f'Port {PORT} is owned by an unrelated PID {owner_pid} '
        f'(worker launcher PID {getattr(_proc, "pid", None)}). '
        'Refusing to submit jobs to a different ComfyUI process.'
    )


def _require_our_api_worker(force=False):
    """Prevent Forge from using a foreign Comfy process on the same local port.

    On Windows netstat resolves the listener; cache only for 1 second so rapid
    history polling does not repeatedly spawn netstat. Any worker exit or PID
    change invalidates the check.
    """
    global _api_owner_verified_at, _api_owner_verified_pid
    with _lock:
        proc = _proc
        if proc is None or proc.poll() is not None:
            raise RuntimeError('Embedded Comfy worker is not running; API call blocked')
        if os.name != 'nt':
            return True
        now = time.monotonic()
        if (not force and _api_owner_verified_pid == proc.pid
                and now - _api_owner_verified_at < 1.0):
            return True
        listener = _listener_pid_windows(PORT)
        if listener is None:
            raise RuntimeError(f'Cannot verify Comfy listener on 127.0.0.1:{PORT}; API call blocked')
        _assert_owned_listener(listener)
        _api_owner_verified_pid = proc.pid
        _api_owner_verified_at = now
        return True


def _session_token(auto_activate=False):
    global _session_active, _session_epoch
    with _lock:
        if auto_activate and not _session_active:
            _session_active=True; _session_epoch += 1
        return _session_epoch if _session_active else None

def _session_current(token):
    with _lock:
        return bool(_session_active and token is not None and token == _session_epoch)

def _release_forge_models_for_h3():
    """Fully release Forge's currently loaded model before the isolated H3 worker starts.

    H3 Full-Comfy does not use Forge's sd_model at all.  Keeping the previous
    checkpoint (for example ANIMA/SDXL/Flux) alive only steals VRAM/RAM from
    Comfy.  Clearing model_data.sd_model + forge_hash mirrors the unload half of
    Forge's forge_model_reload(), while the empty hash guarantees the next
    non-H3 generation reloads its selected checkpoint normally.
    """
    try:
        import gc
        from modules import sd_models
        from backend import memory_management

        had_model = getattr(sd_models.model_data, 'sd_model', None) is not None
        if had_model:
            print('[MiniMax H3 Full Comfy] releasing Forge model before H3 backend...')

        # Drop Forge's strong reference as well as GPU/offload registrations.
        sd_models.model_data.set_sd_model(None)
        # Critical: if this is left equal to the selected model parameters,
        # forge_model_reload() may think a now-missing model is still loaded.
        sd_models.model_data.forge_hash = ''
        memory_management.unload_all_models()
        memory_management.soft_empty_cache()
        gc.collect()

        print('[MiniMax H3 Full Comfy] Forge model memory released for H3')
        return True
    except Exception as exc:
        print(f'[MiniMax H3 Full Comfy] Forge model unload warning: {exc}')
        return False


def activate_session():
    global _session_active, _session_epoch
    with _lock:
        newly_active = not _session_active
        if newly_active:
            _session_active=True; _session_epoch += 1
        token=_session_epoch
    if newly_active:
        # Do this on H3 selection, not on Generate: the user wanted the memory
        # hand-off to happen while configuring the preset, before inference starts.
        _release_forge_models_for_h3()
        # Embedded runtime must discover Forge's selected model folders before startup.
        if _cfg().get('mode') != 'embedded':
            prewarm()
    return token

def shutdown_worker(reason='shutdown', expected_epoch=None):
    """Stop ONLY our own worker; ignore stale asynchronous shutdown requests.

    Hold the lock until the process has exited so a new session cannot race
    with this shutdown and mistake the previous listener for a foreign server.
    """
    global _proc, _log, _watchdog, _worker_job, _worker_job_listener_pid, _active_prompt_id
    global _owner_logged_pair, _api_owner_verified_at, _api_owner_verified_pid
    with _lock:
        if expected_epoch is not None:
            if _session_active or expected_epoch != _session_epoch:
                print('[MiniMax H3 Embedded] stale shutdown ignored')
                return False
        proc = _proc
        _proc = None
        _owner_logged_pair = None
        _api_owner_verified_at = 0.0
        _api_owner_verified_pid = None
        watch = _watchdog
        _watchdog = None
        job = _worker_job
        _worker_job = None
        _worker_job_listener_pid = None
        _active_prompt_id = None
        # KILL_ON_JOB_CLOSE stops the owned Comfy worker AND its descendants,
        # even if the venv launcher or Forge itself exits unexpectedly.
        if job is not None:
            job.close()
        if proc and proc.poll() is None:
            print(f'[MiniMax H3 Embedded] stopping owned worker PID {proc.pid} ({reason})')
            if job is None:
                _kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Handle rare Windows job-assignment races; only our PID is used.
                _kill_process_tree(proc.pid)
            except Exception:
                pass
        if watch and watch.poll() is None:
            try:
                watch.terminate()
                watch.wait(timeout=3)
            except Exception:
                pass
        log = _log
        _log = None
        if log:
            try:
                log.flush()
                log.close()
            except Exception:
                pass
    return True


def deactivate_session(reason='leaving H3 preset'):
    global _session_active, _session_epoch
    with _lock:
        was_active = _session_active
        _session_active = False
        _session_epoch += 1
        epoch = _session_epoch
    if was_active:
        try:
            interrupt()  # targeted to the old prompt; never interrupts a foreign server
        except Exception:
            pass
    threading.Thread(target=shutdown_worker, args=(reason, epoch),
                     name='H3-Comfy-shutdown', daemon=True).start()
    return True


def release_for_native_forge():
    """DEV28: return dedicated Comfy VRAM to the native Forge engine.

    Stops only the worker *launched by this extension*. Unlike /free, process
    exit actually drops its CUDA context and all cached models. Stop is
    synchronous, so native Forge does not race the outgoing worker for VRAM.
    Going back into Comfy will start a fresh worker with ensure_server().

    Also works when ANIMA is active: ANIMA uses the same embedded worker but
    historically didn't activate the MiniMax-only session flag.
    """
    global _session_active, _session_epoch
    with _lock:
        had_session = _session_active
        owned = _proc is not None and _proc.poll() is None
        active_job = owned and _active_prompt_id is not None
        _session_active = False
        _session_epoch += 1
        epoch = _session_epoch

    if active_job:
        # This worker belongs solely to Comfy Neo. A direct owned-process
        # shutdown cancels inference without a potentially 30-second blocking
        # /interrupt HTTP request during a tab switch.
        print('[Comfy Neo] Active Comfy generation cancelled on switch to Forge')

    if owned:
        print('[Comfy Neo] Switching to native Forge: stopping owned Comfy worker to release VRAM')
        released = shutdown_worker('return to native Forge', expected_epoch=epoch)
        if released:
            print('[Comfy Neo] Private Comfy worker stopped; VRAM released to Forge')
        else:
            print('[Comfy Neo] Worker shutdown superseded by a newer Comfy session')
        return released

    if had_session:
        print('[Comfy Neo] Switching to native Forge: no active Comfy worker')
    return True


def _cfg():
    """DEV03: private Python is REQUIRED for embedded mode.

    Never fall back to an external ComfyUI venv or to Forge Python.
    The marker is only written after packages and CUDA pass smoke tests.
    """
    data = json.loads(CONFIG.read_text(encoding='utf-8'))
    if data.get('mode', 'embedded') == 'embedded':
        runtime = ROOT / 'runtime'
        private_python = runtime / 'venv' / 'Scripts' / 'python.exe'
        ready_file = runtime / 'private_env_ready.json'
        main = runtime / 'core' / 'main.py'
        if not (private_python.is_file() and ready_file.is_file() and main.is_file()):
            raise RuntimeError(
                'Forge Comfy Neo private Python is not installed. '
                'Open the Comfy Neo tab and click Install. '
                'No external ComfyUI Python is used.'
            )
        try:
            ready = json.loads(ready_file.read_text(encoding='utf-8'))
            marker_python = Path(ready['python'])
            if marker_python.resolve() != private_python.resolve():
                raise ValueError('runtime moved after install; reinstall private environment')
            if (ready.get('torch') != '2.11.0' or ready.get('torch_cuda') != '13.0'
                    or ready.get('comfy_source') != 'bdb0816a292d221efeb6ec4ae41bcdff3f91fc86'):
                raise ValueError('unexpected private runtime version')
        except Exception as exc:
            raise RuntimeError(
                'Forge Comfy Neo private Python setup is incomplete or moved. '
                'See README Recovery; never move the private venv without reinstalling.'
            ) from exc
        data['comfy_root'] = str(runtime / 'core')
        data['comfy_python'] = str(private_python)
    return data


def _prepare_embedded_model_paths(checkpoint, component_files):
    """Make actual Forge-selected H3 models visible to the bundled Comfy core.

    Comfy has its own model discovery; simply pointing it at Forge's models root
    is insufficient when a module lives elsewhere. This file is written before
    starting the worker; it isn't changed while a worker is running.
    """
    cfg = _cfg()
    if cfg.get('mode', 'embedded') != 'embedded':
        return None
    root = Path(cfg['comfy_root'])
    if not (root / 'main.py').is_file():
        raise RuntimeError(
            'Embedded Comfy runtime is not installed. Run setup_embedded_runtime.bat '
            'from the extension directory first.'
        )
    from .models import resolve_components
    # Selected module filenames can come from Forge modules outside its root.
    paths = {'diffusion_models': set(), 'text_encoders': set(), 'vae': set(), 'loras': set()}
    if checkpoint:
        selected = Path(checkpoint)
        if selected.is_file():
            paths['diffusion_models'].add(str(selected.parent.resolve()))
    for path in component_files:
        p = Path(path)
        low = p.name.lower()
        if 'qwen' in low or 'encoder' in low:
            role = 'text_encoders'
        elif 'vae' in low:
            role = 'vae'
        else:
            continue
        if p.is_file():
            paths[role].add(str(p.parent.resolve()))
    # Defaults for typical Forge layouts; nested folders are recursively searched.
    forge_models = ROOT.parent.parent / 'models'
    for key, dirs in {
        'diffusion_models': ['Stable-diffusion', 'diffusion_models', 'unet'],
        'text_encoders': ['text_encoder', 'text_encoders', 'clip'],
        'vae': ['VAE', 'vae'],
        'loras': ['Lora', 'loras'],
    }.items():
        for directory in dirs:
            candidate = forge_models / directory
            if candidate.is_dir():
                paths[key].add(str(candidate.resolve()))
    # Additional optional directories for users with a central model collection.
    for key, vals in (cfg.get('extra_model_dirs') or {}).items():
        if key in paths:
            for path in vals:
                if Path(path).is_dir():
                    paths[key].add(str(Path(path).resolve()))
    # An extra paths YAML with absolute paths is supported by official ComfyUI.
    import yaml
    categories = {key: '\n'.join(sorted(vals)) for key, vals in paths.items() if vals}
    pathfile = ROOT / 'runtime' / 'extra_model_paths.yaml'
    pathfile.write_text(yaml.safe_dump({'forge_models': categories}, allow_unicode=True), encoding='utf-8')
    return pathfile

def _url(path): return f'http://127.0.0.1:{PORT}{path}'

def _alive():
    try:
        with urllib.request.urlopen(_url('/system_stats'), timeout=.35) as r: return r.status == 200
    except Exception: return False

def prewarm():
    """Start Comfy without blocking Forge UI. Models remain under Comfy's dynamic VRAM manager."""
    threading.Thread(target=ensure_server, name='H3-Comfy-prewarm', daemon=True).start()

def ensure_server(timeout=90):
    global _proc, _log, _logpath
    global _watchdog, _worker_job, _worker_job_listener_pid, _owner_logged_pair, _api_owner_verified_at, _api_owner_verified_pid
    with _lock:
        # Pick a separate port BEFORE the first /system_stats probe. A foreign
        # ComfyUI on 8191 must never be mistaken for our private worker.
        _auto_select_worker_port()
        if _alive():
            if _proc and _proc.poll() is None:
                if os.name == 'nt':
                    owner = _listener_pid_windows(PORT)
                    if owner is not None:
                        _assert_owned_listener(owner)
                return True
            # Do not kill another process: it may belong to a different Forge
            # installation or a separately started ComfyUI.
            raise RuntimeError(
                f'Embedded Comfy cannot start: port {PORT} is already owned by '
                'another process. Stop the other server or change the port. '
                'Forge Comfy Neo will not terminate unrelated processes.'
            )
        if _proc and _proc.poll() is not None:
            # A dead venv redirector may have left child interpreters alive;
            # closing our private job also terminates those descendants.
            if _worker_job is not None:
                _worker_job.close()
                _worker_job = None
            _worker_job_listener_pid = None
            if _watchdog and _watchdog.poll() is None:
                _watchdog.terminate()
            _watchdog = None
            _proc = None
        if os.name == 'nt':
            occupant = _listener_pid_windows(PORT)
            if occupant is not None:
                if _proc and _proc.poll() is None and _listener_belongs_to_worker(occupant):
                    # Another Forge request raced with our own worker startup.
                    # Keep waiting for /system_stats; do not treat our child as foreign.
                    pass
                else:
                    raise RuntimeError(
                        f'Embedded Comfy port {PORT} is occupied by unrelated PID {occupant}. '
                        'Stop that server before starting MiniMax H3. '
                        'Forge Comfy Neo never terminates unrelated processes.'
                    )
        cfg=_cfg(); py=Path(cfg['comfy_python']); root=Path(cfg['comfy_root'])
        main=root/'main.py'
        if not py.is_file() or not main.is_file():
            if cfg.get('mode', 'embedded') == 'embedded':
                raise RuntimeError('Embedded runtime not ready. Run setup_private_runtime.bat in this extension. Missing: '+str(py)+' or '+str(main))
            raise RuntimeError(f'Comfy path is invalid: {py} / {main}')
        if _proc and _proc.poll() is None:
            pass
        else:
            logpath=Path(os.getenv('TEMP','.' ))/f'forge-comfy-neo-backend-{os.getpid()}.log'
            _logpath=logpath
            _log=open(logpath,'w',encoding='utf-8',buffering=1)  # DEV04: log for this worker only
            cmd=[str(py),'-u',str(main),'--listen','127.0.0.1','--port',str(PORT),'--disable-auto-launch']
            if cfg.get('mode', 'embedded') == 'embedded':
                cmd += ['--disable-all-custom-nodes', '--disable-assets', '--disable-partner-nodes']
                pathfile = ROOT / 'runtime' / 'extra_model_paths.yaml'
                if pathfile.is_file():
                    cmd += ['--extra-model-paths-config', str(pathfile)]
            env=os.environ.copy()
            # No accidental import from Forge, system Python or external Comfy.
            for variable in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
                env.pop(variable, None)
            env['PYTHONUNBUFFERED']='1'
            env['PYTHONNOUSERSITE']='1' 
            kwargs=dict(cwd=str(root),stdout=_log,stderr=subprocess.STDOUT,env=env)
            if os.name == 'nt':
                kwargs.update(hidden_windows_kwargs())
            else:
                kwargs['start_new_session']=True
            _proc=subprocess.Popen(cmd,**kwargs)
            _worker_job_listener_pid = None
            _owner_logged_pair = None
            _api_owner_verified_at = 0.0
            _api_owner_verified_pid = None
            print(f'[MiniMax H3 Embedded] private runtime Python: {py}')
            # On Windows prefer the OS-managed lifetime guarantee. No extra
            # watchdog Python process is required, and Forge's abrupt exit
            # closes this handle even when Python atexit callbacks do not run.
            if os.name == 'nt':
                candidate_job = None
                try:
                    candidate_job = WindowsKillJob()
                    candidate_job.assign(_proc)
                    _worker_job = candidate_job
                    print('[Comfy Neo] Windows Job Object owns private worker process tree')
                except Exception as exc:
                    if candidate_job is not None:
                        candidate_job.close()
                    _worker_job = None
                    print(f'[Comfy Neo] Windows job unavailable; using parent-watchdog fallback: {exc}')

            # Watchdog fallback for older/restricted Windows environments and
            # non-Windows systems. CREATE_NO_WINDOW must NOT be combined with
            # DETACHED_PROCESS (Windows ignores the former in that case).
            if _worker_job is None:
                helper=Path(__file__).with_name('comfy_parent_watchdog.py')
                try:
                    watchdog_python = sys.executable
                    watchdog_kwargs = {}
                    if os.name == 'nt':
                        pyw = Path(sys.executable).with_name('pythonw.exe')
                        if pyw.is_file():
                            watchdog_python = str(pyw)
                        watchdog_kwargs.update(hidden_windows_kwargs())
                    _watchdog=subprocess.Popen(
                        [watchdog_python,str(helper),'--parent',str(os.getpid()),'--child',str(_proc.pid)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        close_fds=True, **watchdog_kwargs)
                except Exception as exc:
                    print(f'[MiniMax H3 Full Comfy] parent watchdog unavailable: {exc}')
            print(f'[MiniMax H3 Full Comfy] warm worker starting on {PORT}; log: {logpath}')
    end=time.time()+timeout
    while time.time()<end:
        if _alive():
            if os.name == 'nt' and _proc:
                owner = _listener_pid_windows(PORT)
                if owner is not None:
                    _assert_owned_listener(owner)
            print('[MiniMax H3 Embedded] worker ready')
            return True
        if _proc and _proc.poll() is not None:
            reason = f'Comfy worker exited during startup (exit code {_proc.returncode}).'
            if _logpath:
                try:
                    recent = Path(_logpath).read_text(encoding='utf-8', errors='replace').splitlines()[-35:]
                    if recent:
                        reason += '\nRecent worker log:\n' + '\n'.join(recent)
                except OSError:
                    pass
            raise RuntimeError(reason)
        time.sleep(.5)
    raise TimeoutError('Timed out waiting for Comfy worker')

def _worker_failure_details(prefix):
    """Include only this worker's recent log if it dies unexpectedly."""
    with _lock:
        proc = _proc
        logpath = _logpath
    if proc is None:
        return prefix + ' (embedded worker is not running)'
    rc = proc.poll()
    if rc is None:
        return prefix + ' (worker process is still running)'
    msg = prefix + f' (embedded worker exited with code {rc})'
    if logpath:
        try:
            tail = Path(logpath).read_text(encoding='utf-8', errors='replace').splitlines()[-45:]
            if tail:
                msg += '\nLast worker log lines:\n' + '\n'.join(tail)
        except OSError:
            pass
    return msg

def _post(path,obj):
    _require_our_api_worker()
    req=urllib.request.Request(_url(path),data=json.dumps(obj).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            raw=r.read().decode('utf-8', errors='replace')
            # /interrupt legitimately returns an empty 200 response.
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw=e.read().decode('utf-8', errors='replace')
        print(f'[MiniMax H3 Full Comfy] API ERROR {e.code} {e.reason} on {path}')
        print('[MiniMax H3 Full Comfy] ---- Comfy response begin ----')
        print(raw or '<empty response body>')
        print('[MiniMax H3 Full Comfy] ---- Comfy response end ----')
        try:
            parsed=json.loads(raw)
            detail=json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            detail=raw
        raise RuntimeError(f'Comfy API rejected the workflow (HTTP {e.code}).\n{detail}') from e
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise RuntimeError(_worker_failure_details(f'Comfy POST {path} failed: {exc}')) from exc

def _get(path,timeout=30):
    _require_our_api_worker()
    try:
        with urllib.request.urlopen(_url(path),timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise RuntimeError(_worker_failure_details(f'Comfy GET {path} failed: {exc}')) from exc

def _choice_values(class_type, input_name):
    """Ask the running Comfy instance for the exact model names it accepts."""
    try:
        info = _get('/object_info/' + class_type)
        spec = info[class_type]['input']['required'][input_name][0]
        return list(spec) if isinstance(spec, list) else []
    except Exception as e:
        print(f'[MiniMax H3 Full Comfy] could not query {class_type}.{input_name}: {e}')
        return []

def _resolve_model_name(class_type, input_name, selected, fallback):
    """Resolve Forge basename/path to Comfy's exact relative name, including subfolders."""
    selected = str(selected or '').replace('/', '\\')
    fallback = str(fallback or '').replace('/', '\\')
    choices = _choice_values(class_type, input_name)
    if not choices:
        return fallback or selected
    # Exact relative path first.
    for candidate in (selected, fallback):
        if candidate and candidate in choices:
            return candidate
    # Then match only by basename (Forge normally gives us an absolute path or bare filename).
    wanted = os.path.basename(selected.replace('\\', os.sep)).lower() if selected else ''
    if wanted:
        hits = [x for x in choices if os.path.basename(str(x).replace('\\', os.sep)).lower() == wanted]
        if len(hits) == 1:
            print(f'[MiniMax H3 Full Comfy] resolved {os.path.basename(selected)} -> {hits[0]}')
            return hits[0]
        if len(hits) > 1:
            print(f'[MiniMax H3 Full Comfy] multiple matches for {wanted}; using {hits[0]}')
            return hits[0]
    # Keep the known-good workflow value when the selected Forge file is not present in Comfy.
    if fallback in choices:
        print(f'[MiniMax H3 Full Comfy] {wanted or selected} is not visible to Comfy; keeping workflow model {fallback}')
        return fallback
    return selected or fallback


def interrupt():
    """Cancel our active prompt without globally interrupting an unrelated job."""
    with _lock:
        prompt_id = _active_prompt_id
        proc = _proc
        owned = proc is not None and proc.poll() is None
    if not (owned and prompt_id):
        print('[MiniMax H3 Embedded] STOP: no active owned prompt')
        return True  # preserve legacy Forge Stop callback behavior
    try:
        # Targeted interrupt handles running prompts; queue deletion handles
        # prompts submitted but not yet executing.
        _post('/interrupt', {'prompt_id': str(prompt_id)})
        _post('/queue', {'delete': [str(prompt_id)]})
        print(f'[MiniMax H3 Embedded] STOP requested for {prompt_id}')
    except Exception as exc:
        print(f'[MiniMax H3 Embedded] STOP failed for {prompt_id}: {exc}')
    return True


def free_embedded_models():
    """Ask OUR active worker to unload models and clear its execution cache.

    Comfy's /free queues cleanup asynchronously; the operating system may keep
    file-backed memory cached. Stopping the process releases its allocations.
    """
    with _lock:
        owned = _proc is not None and _proc.poll() is None
        busy = _active_prompt_id is not None
    if not owned:
        print('[MiniMax H3 Embedded] No owned worker; nothing to free')
        return False
    if busy:
        print('[MiniMax H3 Embedded] Cannot unload models during active generation')
        return False
    try:
        _post('/free', {'unload_models': True, 'free_memory': True})
        print('[MiniMax H3 Embedded] model/cache unload requested')
        return True
    except Exception as exc:
        print(f'[MiniMax H3 Embedded] memory cleanup failed: {exc}')
        return False


def _start_ws_relay(client_id):
    """Start a websocket event relay using ComfyUI's own Python/aiohttp.

    This avoids adding websocket packages to Forge's venv and gives us the same
    progress events the Comfy frontend receives.
    """
    _require_our_api_worker()
    cfg=_cfg(); py=Path(cfg['comfy_python'])
    helper=Path(__file__).with_name('comfy_ws_events.py')
    q=queue.Queue()
    env = os.environ.copy()
    for variable in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
        env.pop(variable, None)
    env['PYTHONNOUSERSITE'] = '1'
    proc=subprocess.Popen(
        [str(py), '-u', str(helper), '--port', str(PORT), '--client', str(client_id)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
        errors='replace', bufsize=1, env=env,
        **hidden_windows_kwargs())
    # An auxiliary progress relay must be owned as well. Otherwise closing
    # Forge during a long generation may leave this short-lived Python behind.
    if os.name == 'nt':
        with _lock:
            if _worker_job is not None:
                try:
                    _worker_job.assign(proc)
                except Exception:
                    proc.terminate()
                    raise

    def reader():
        try:
            for line in proc.stdout:
                q.put(line.rstrip('\r\n'))
        finally:
            q.put('__H3_WS_EOF__')
    threading.Thread(target=reader, name='H3-Comfy-ws-reader', daemon=True).start()

    deadline=time.time()+12
    stash=[]
    while time.time()<deadline:
        try: line=q.get(timeout=.25)
        except queue.Empty: continue
        if line == '__H3_WS_READY__':
            for x in stash: q.put(x)
            return proc,q
        if line.startswith('__H3_WS_ERROR__') or line == '__H3_WS_EOF__':
            try: proc.terminate()
            except Exception: pass
            raise RuntimeError(f'Comfy websocket relay failed: {line}')
        stash.append(line)
    try: proc.terminate()
    except Exception: pass
    raise TimeoutError('Timed out connecting to Comfy websocket')


def _stop_ws_relay(proc):
    if not proc: return
    try:
        if proc.poll() is None: proc.terminate()
    except Exception:
        pass

def _fmt_clock(seconds):
    seconds=max(0,int(seconds)); m,s=divmod(seconds,60)
    return f'{m:02d}:{s:02d}'

def _event_prompt_id(data):
    return str((data or {}).get('prompt_id') or '')

def _print_ws_event(evt, pid, graph, progress_state):
    """Print useful Comfy websocket events. Return terminal state or None."""
    if not isinstance(evt,dict): return None
    typ=str(evt.get('type') or '')
    data=evt.get('data') or {}
    event_pid=_event_prompt_id(data)
    if event_pid and event_pid != str(pid): return None

    if typ == 'execution_start':
        print('[MiniMax H3 Full Comfy] Comfy execution started')
    elif typ == 'executing':
        node=data.get('node')
        if node is None:
            return 'executing_done'
        node=str(node); cls=(graph.get(node) or {}).get('class_type','')
        if node != progress_state.get('node'):
            progress_state['node']=node
            labels={
                'CLIPLoader':'loading text encoder', 'UNETLoader':'loading diffusion model',
                'LoraLoaderModelOnly':'applying Turbo LoRA', 'MiniMaxH3ReferenceToVideo':'encoding prompt / preparing latent',
                'MiniMaxH3ImageToVideo':'encoding prompt / preparing latent', 'SamplerCustomAdvanced':'sampling', 'VAEDecode':'video VAE decode',
                'VAEDecodeAudio':'audio VAE decode', 'CreateVideo':'assembling video', 'SaveVideo':'saving MP4'
            }
            if cls == 'SamplerCustomAdvanced':
                now=time.time(); progress_state['sample_start']=now; progress_state['last_t']=now
            if cls in labels:
                print(f'[MiniMax H3 Full Comfy] {labels[cls]}...')
    elif typ == 'progress':
        try:
            value=int(data.get('value')); maximum=int(data.get('max')); node=str(data.get('node') or '')
        except Exception:
            return None
        # The H3 graph's sampler is node 105:14. Keep a max==steps fallback for future graph revisions.
        is_sampler = node == '105:14' or maximum == int(progress_state.get('steps') or -1)
        if is_sampler and maximum > 0 and value > 0:
            key=(value,maximum)
            if key != progress_state.get('last'):
                now=time.time(); start=progress_state.setdefault('sample_start', now)
                last_t=progress_state.get('last_t')
                step_dt=(now-last_t) if last_t else (now-start)
                progress_state['last_t']=now; progress_state['last']=key
                elapsed=now-start
                avg=elapsed/value if value else 0
                eta=max(0.0, avg*(maximum-value))
                speed=step_dt if step_dt > 0 else avg
                print(f'[MiniMax H3 Full Comfy] {value}/{maximum} '
                      f'[{_fmt_clock(elapsed)}<{_fmt_clock(eta)}, {speed:.2f}s/it]')
    elif typ == 'execution_success':
        return 'success'
    elif typ == 'execution_interrupted':
        return 'interrupted'
    elif typ == 'execution_error':
        msg=data.get('exception_message') or data.get('exception_type') or data
        print(f'[MiniMax H3 Full Comfy] execution error: {msg}')
        return 'error'
    return None

def _progress_tail(start_pos):
    """Mirror useful Comfy sampler/progress lines into the Forge console."""
    if not _logpath or not Path(_logpath).is_file():
        return start_pos
    try:
        with open(_logpath, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(start_pos)
            chunk=f.read()
            pos=f.tell()
        for raw in chunk.splitlines():
            line=raw.strip()
            low=line.lower()
            if (('%' in line and ('it/s' in low or 's/it' in low or '/it' in low))
                    or 'prompt executed in' in low or 'vae' in low and ('load' in low or 'decode' in low)):
                print(f'[MiniMax H3 Comfy] {line}')
        return pos
    except Exception:
        return start_pos

def _decode_data_url(data_url):
    if not isinstance(data_url, str) or not data_url.startswith('data:') or ',' not in data_url:
        raise RuntimeError('Invalid browser image payload')
    meta, raw = data_url.split(',', 1)
    ext = '.png'
    if ';base64' in meta:
        blob = base64.b64decode(raw)
    else:
        blob = urllib.parse.unquote_to_bytes(raw)
    low = meta.lower()
    if 'image/jpeg' in low:
        ext = '.jpg'
    elif 'image/webp' in low:
        ext = '.webp'
    elif 'image/gif' in low:
        ext = '.gif'
    return blob, ext


def _upload_browser_image(data_url, stem='h3_input'):
    _require_our_api_worker()
    blob, ext = _decode_data_url(data_url)
    filename = f'{stem}_{uuid.uuid4().hex[:8]}{ext}'
    boundary = '----H3Boundary' + uuid.uuid4().hex
    crlf = b'\r\n'
    body = []
    def add_field(name, value):
        body.extend([
            f'--{boundary}'.encode(), crlf,
            f'Content-Disposition: form-data; name="{name}"'.encode(), crlf, crlf,
            str(value).encode('utf-8'), crlf,
        ])
    body.extend([
        f'--{boundary}'.encode(), crlf,
        f'Content-Disposition: form-data; name="image"; filename="{filename}"'.encode(), crlf,
        b'Content-Type: application/octet-stream', crlf, crlf,
        blob, crlf,
    ])
    add_field('type', 'input')
    add_field('overwrite', 'true')
    body.extend([f'--{boundary}--'.encode(), crlf])
    payload = b''.join(body)
    req = urllib.request.Request(_url('/upload/image'), data=payload,
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode('utf-8', errors='replace') or '{}')


def _parse_media_payload(media_json):
    if not media_json:
        return {'init_image': None, 'stitch_images': []}
    try:
        payload = json.loads(media_json)
    except Exception as exc:
        raise RuntimeError(f'Invalid H3 media payload: {exc}') from exc
    if not isinstance(payload, dict):
        return {'init_image': None, 'stitch_images': []}
    init_image = payload.get('init_image') if isinstance(payload.get('init_image'), str) else None
    stitch_images = [x for x in (payload.get('stitch_images') or []) if isinstance(x, str)]
    resize_by = payload.get('resize_by')
    return {'init_image': init_image, 'stitch_images': stitch_images, 'resize_by': resize_by}


def _resolve_h3_dimensions(width, height, media):
    """Respect Forge img2img Resize by without altering Resize to / txt2img.

    Forge's Resize by tab leaves img2img_width / img2img_height unchanged.
    The bridge therefore needs the original img2img image + scale value.
    """
    width, height = int(width), int(height)
    factor = media.get('resize_by')
    if factor is None:
        return width, height

    import math
    try:
        factor = float(factor)
    except (TypeError, ValueError) as exc:
        raise RuntimeError('MiniMax H3: invalid img2img Resize by scale') from exc
    if not math.isfinite(factor) or not 0.5 <= factor <= 4.0:
        raise RuntimeError(f'MiniMax H3: img2img Resize by scale out of range: {factor}')
    if not media.get('init_image'):
        raise RuntimeError('MiniMax H3: Resize by requires an uploaded img2img input image')

    try:
        from io import BytesIO
        from PIL import Image
        blob, _ = _decode_data_url(media['init_image'])
        with Image.open(BytesIO(blob)) as picture:
            source_width, source_height = picture.size
    except Exception as exc:
        raise RuntimeError(f'MiniMax H3: could not read img2img source dimensions: {exc}') from exc
    if source_width < 1 or source_height < 1:
        raise RuntimeError('MiniMax H3: empty img2img source dimensions')

    # Match Forge's resize_from_to_html() preview, which snaps sizes to its
    # configured Resolution Step. Comfy's separate /32 requirement is checked
    # by generate() below (no silent rounding to a different /32 size).
    try:
        from modules import shared
        step = int(getattr(shared.opts, 'res_step', 8))
    except ImportError:
        step = 8  # Only for isolated tests outside Forge
    if step < 1:
        step = 8
    width = math.floor(source_width * factor / step + 0.5) * step
    height = math.floor(source_height * factor / step + 0.5) * step
    print(f'[MiniMax H3 Full Comfy] img2img Resize by {factor:g}: '
          f'{source_width}x{source_height} -> {width}x{height} (step {step})')
    return width, height


def _add_load_image_nodes(graph, images, prefix='h3_load'):
    refs = []
    for index, data_url in enumerate(images or []):
        uploaded = _upload_browser_image(data_url, f'{prefix}_{index+1}')
        node_id = f'{prefix}_{index+1}'
        graph[node_id] = {
            'class_type': 'LoadImage',
            'inputs': {
                'image': uploaded.get('name') or '',
                'upload': 'image',
            },
        }
        refs.append((node_id, 0))
    return refs


def _activate_fl2va_graph(graph, prompt, width, height, frames, first_image=None, last_image=None):
    if '105:104' not in graph:
        raise RuntimeError('FL2VA API template is missing MiniMaxH3ImageToVideo (105:104)')
    graph['105:104']['inputs'].update({
        'clip': ['105:13', 0],
        'vae': ['105:11', 0],
        'prompt': str(prompt),
        'width': int(width),
        'height': int(height),
        'length': int(frames),
    })
    if first_image:
        first_node, _ = _add_load_image_nodes(graph, [first_image], 'h3_first')[0]
        graph['105:104']['inputs']['first_frame'] = [first_node, 0]
    else:
        graph['105:104']['inputs'].pop('first_frame', None)
    if last_image:
        last_node, _ = _add_load_image_nodes(graph, [last_image], 'h3_last')[0]
        graph['105:104']['inputs']['last_frame'] = [last_node, 0]
    else:
        graph['105:104']['inputs'].pop('last_frame', None)
    graph['105:16']['inputs']['conditioning'] = ['105:104', 0]
    graph['105:14']['inputs']['latent_image'] = ['105:104', 1]
    return graph


def _activate_ref2va_graph(graph, prompt, width, height, frames, init_image=None, stitch_images=None):
    """Hard-bind the main img2img input to slot 0, then append ImageStitch refs.

    The two sources stay separate all the way until the Comfy API graph is built;
    gallery thumbnails cannot silently replace the primary image.
    """
    stitch_images = list(stitch_images or [])
    graph['105:128']['inputs'].update({
        'prompt': str(prompt),
        'width': int(width),
        'height': int(height),
        'length': int(frames),
        'ref_image_size': graph['105:128']['inputs'].get('ref_image_size', 'match') or 'match',
    })
    for key in list(graph['105:128']['inputs']):
        if str(key).startswith('ref_images.ref_image_'):
            graph['105:128']['inputs'].pop(key, None)
    slot = 0
    if init_image:
        main_node = _add_load_image_nodes(graph, [init_image], 'h3_main_img2img')[0]
        graph['105:128']['inputs']['ref_images.ref_image_0'] = list(main_node)
        print('[MiniMax H3 Full Comfy] ref_image_0 <- main img2img input')
        slot = 1
    for node_id, output_idx in _add_load_image_nodes(graph, stitch_images, 'h3_stitch'):
        graph['105:128']['inputs'][f'ref_images.ref_image_{slot}'] = [node_id, output_idx]
        slot += 1
    print(f'[MiniMax H3 Full Comfy] Ref2VA slots: main={int(bool(init_image))}, ImageStitch={len(stitch_images)}, total={slot}')
    return graph


def generate(prompt,width,height,frames,seed,steps,checkpoint,modules,media_json='',sampler='res_multistep',scheduler='simple'):
    global _active_prompt_id
    # Resolve img2img Resize by BEFORE validating /32 and before starting the
    # headless worker. Resize to and txt2img have no resize_by in their payload.
    media = _parse_media_payload(media_json)
    width, height = _resolve_h3_dimensions(width, height, media)
    bad=[]
    if width % 32:
        bad.append(f'width={width}')
    if height % 32:
        bad.append(f'height={height}')
    if bad:
        def near32(v):
            lo=(v//32)*32
            hi=lo+32
            return (lo,hi)
        wl,wh=near32(width); hl,hh=near32(height)
        raise RuntimeError(
            'MiniMax H3: width и height должны быть кратны 32. '
            f'Получено: {width}x{height}. '
            f'Ближайшие допустимые: width {wl} или {wh}; height {hl} или {hh}. '
            'Размер автоматически не изменён.'
        )
    token=_session_token(auto_activate=True)
    from .integration import module_paths
    from .models import resolve_components
    component_files = module_paths(modules)
    components = resolve_components(checkpoint, component_files)
    _prepare_embedded_model_paths(checkpoint, component_files)
    ensure_server()
    if not _session_current(token):
        return None
    template = REF_TEMPLATE if components.mode == 'ref2va' else FL_TEMPLATE
    graph = json.loads(template.read_text(encoding='utf-8'))
    print(f'[MiniMax H3 Full Comfy] selected {components.mode} API workflow: {template.name}')
    init_image = media.get('init_image')
    stitch_images = list(media.get('stitch_images') or [])
    if components.mode == 'ref2va':
        ref_images = ([init_image] if init_image else []) + stitch_images
        if len(ref_images) > 9:
            raise RuntimeError(f'MiniMax H3 Ref2VA takes up to 9 reference pictures; got {len(ref_images)}.')
        if ref_images:
            print(f'[MiniMax H3 Full Comfy] reference mode with {len(ref_images)} picture(s)')
            graph = _activate_ref2va_graph(graph, prompt, width, height, frames, init_image, stitch_images)
        else:
            graph['105:128']['inputs']['prompt']=str(prompt)
    else:
        first_image = init_image
        last_image = stitch_images[0] if stitch_images else None
        if first_image or last_image:
            extra = max(0, len(stitch_images) - (1 if last_image else 0))
            if extra:
                print(f'[MiniMax H3 Full Comfy] FL2VA uses the first ImageStitch picture as last frame; ignoring {extra} extra picture(s)')
            print(f'[MiniMax H3 Full Comfy] first/last-frame mode: first={bool(first_image)} last={bool(last_image)}')
            graph = _activate_fl2va_graph(graph, prompt, width, height, frames, first_image, last_image)
        else:
            graph['105:104']['inputs']['prompt']=str(prompt)
    # Forge dimensions are authoritative.  Disable the workflow's 1:1 ratio lock
    # so 1280x720 cannot silently become 1280x1280.
    graph['127']['inputs']['Mode']='Override'
    graph['127']['inputs']['Ratio Lock']=False
    graph['127']['inputs']['Width']=width; graph['127']['inputs']['Height']=height
    graph['105:107']['inputs']['expression']=str(int(frames))
    if '105:128' in graph:
        graph['105:128']['inputs']['width']=width
        graph['105:128']['inputs']['height']=height
        graph['105:128']['inputs']['length']=int(frames)
    if '105:104' in graph:
        graph['105:104']['inputs']['width']=width
        graph['105:104']['inputs']['height']=height
        graph['105:104']['inputs']['length']=int(frames)
    requested_seed=int(seed)
    actual_seed=requested_seed if requested_seed >= 0 else secrets.randbelow(2**53)  # gr.Number/JS-safe for Reuse Seed
    graph['105:15']['inputs']['noise_seed']=actual_seed
    if requested_seed < 0:
        print(f'[MiniMax H3 Full Comfy] Forge seed -1 -> random Comfy seed {actual_seed}')
    graph['105:125']['inputs']['value']=int(steps)

    selected_clip = selected_audio = selected_video = None
    allmods=[str(x) for x in (modules or [])]
    for x in allmods:
        low=x.lower()
        if 'qwen3' in low: selected_clip=x
        elif 'audio_vae' in low: selected_audio=x
        elif 'video_vae' in low: selected_video=x

    graph['105:13']['inputs']['clip_name'] = _resolve_model_name(
        'CLIPLoader','clip_name',selected_clip,graph['105:13']['inputs']['clip_name'])
    graph['105:24']['inputs']['vae_name'] = _resolve_model_name(
        'VAELoader','vae_name',selected_audio,graph['105:24']['inputs']['vae_name'])
    graph['105:11']['inputs']['vae_name'] = _resolve_model_name(
        'VAELoader','vae_name',selected_video,graph['105:11']['inputs']['vae_name'])
    graph['105:6']['inputs']['unet_name'] = _resolve_model_name(
        'UNETLoader','unet_name',checkpoint,graph['105:6']['inputs']['unet_name'])

    if '105:121' in graph:
        graph['105:121']['inputs']['lora_name'] = _resolve_model_name(
            'LoraLoaderModelOnly','lora_name',graph['105:121']['inputs']['lora_name'],graph['105:121']['inputs']['lora_name'])

    print('[MiniMax H3 Full Comfy] resolved models:',
          graph['105:6']['inputs']['unet_name'], '|',
          graph['105:13']['inputs']['clip_name'], '|',
          graph['105:24']['inputs']['vae_name'], '|',
          graph['105:11']['inputs']['vae_name'])
    prefix=f'video/Forge_H3_{actual_seed}_{uuid.uuid4().hex[:8]}'
    graph['92']['inputs']['filename_prefix']=prefix

    client=str(uuid.uuid4())
    started_at=time.time()
    ws_proc=ws_q=None
    pid=None
    try:
        try:
            ws_proc,ws_q=_start_ws_relay(client)
            print('[MiniMax H3 Full Comfy] websocket progress connected')
        except Exception as exc:
            print(f'[MiniMax H3 Full Comfy] websocket progress unavailable, history fallback: {exc}')

        # DEV14: Attention is a Comfy Neo setting, not a global Forge option.
        # Stable is an unchanged compiled graph (no extra attention nodes).
        from .workflow_native import (
            compile_h3_graph, ATTENTION_STABLE, ATTENTION_CK,
            normalize_attention_mode,
        )
        from modules import shared
        attention_mode = normalize_attention_mode(
            str(getattr(shared.opts, 'h3_attention_mode', ATTENTION_STABLE))
        )
        graph=compile_h3_graph(graph, steps=steps, width=width, height=height,
                               frames=frames, attention_mode=attention_mode)
        from .comfy_sampler_ui import validate
        actual_sampler, actual_scheduler = validate(sampler, scheduler)
        if graph.get('105:17', {}).get('class_type') != 'KSamplerSelect' or graph.get('105:9', {}).get('class_type') != 'BasicScheduler':
            raise RuntimeError('MiniMax H3 expected sampler nodes are missing; refusing a partial graph change')
        graph['105:17']['inputs']['sampler_name'] = actual_sampler
        graph['105:9']['inputs']['scheduler'] = actual_scheduler
        print(f'[Comfy Neo] H3 seed={actual_seed} sampler={actual_sampler} scheduler={actual_scheduler}', flush=True)
        print(f'[Comfy Neo] attention mode: {attention_mode}')
        if attention_mode == ATTENTION_CK:
            print('[Comfy Neo] ModelAttentionBackend -> comfy kitchen attention'
                  ' (EXPERIMENTAL; compatibility is not guaranteed)')
        pid=_post('/prompt',{'prompt':graph,'client_id':client})['prompt_id']
        with _lock:
            _active_prompt_id = pid
        print(f'[MiniMax H3 Full Comfy] submitted {pid}: {frames} frames {width}x{height}')
        progress_state={'steps': int(steps)}
        terminal=None
        end=time.time()+3600

        while time.time()<end:
            if not _session_current(token):
                print('[MiniMax H3 Full Comfy] stale H3 job discarded after preset/session change')
                return None
            if ws_q is not None:
                while True:
                    try:
                        raw=ws_q.get_nowait()
                    except queue.Empty:
                        break
                    if not raw or raw.startswith('__H3_WS_'):
                        continue
                    try:
                        evt=json.loads(raw)
                    except Exception:
                        continue
                    term=_print_ws_event(evt,pid,graph,progress_state)
                    if term in ('interrupted','error','success','executing_done'):
                        terminal=term

            if terminal == 'interrupted':
                print('[MiniMax H3 Full Comfy] generation stopped by user')
                return None

            try:
                h=_get('/history/'+pid)
            except Exception:
                if not _session_current(token):
                    print('[MiniMax H3 Full Comfy] worker stopped after leaving H3; old result ignored')
                    return None
                raise
            if pid in h:
                item=h[pid]
                status=item.get('status',{})
                messages=status.get('messages',[]) or []
                interrupted=status.get('status_str') in ('interrupted','cancelled') or any(
                    isinstance(m,(list,tuple)) and m and str(m[0]).lower()=='execution_interrupted'
                    for m in messages)
                if interrupted:
                    print('[MiniMax H3 Full Comfy] generation stopped by user')
                    return None
                if status.get('status_str')=='error' or terminal == 'error':
                    raise RuntimeError(str(status))

                outputs=item.get('outputs',{})
                for out in outputs.values():
                    for key in ('videos','gifs','images'):
                        for f in out.get(key,[]) or []:
                            fn=f.get('filename','')
                            if fn.lower().endswith(('.mp4','.webm','.mov','.mkv')):
                                root=Path(_cfg()['comfy_root'])
                                path=root/'output'/f.get('subfolder','')/fn
                                if path.is_file():
                                    if not _session_current(token): return None
                                    elapsed=time.time()-started_at
                                    print(f'[MiniMax H3 Full Comfy] done in {elapsed:.1f}s: {path}')
                                    return str(path)

                def walk(v):
                    if isinstance(v,dict):
                        for z in v.values():
                            r=walk(z)
                            if r:return r
                    elif isinstance(v,list):
                        for z in v:
                            r=walk(z)
                            if r:return r
                    elif isinstance(v,str) and v.lower().endswith('.mp4'):
                        return v
                fn=walk(outputs)
                if fn:
                    path=Path(_cfg()['comfy_root'])/'output'/fn
                    if path.is_file():
                        if not _session_current(token): return None
                        elapsed=time.time()-started_at
                        print(f'[MiniMax H3 Full Comfy] done in {elapsed:.1f}s: {path}')
                        return str(path)
                if status.get('completed') or terminal in ('success','executing_done'):
                    raise RuntimeError('Comfy finished but no MP4 was found in history output')

            time.sleep(.15)

        raise TimeoutError('H3 Comfy generation timed out')
    finally:
        with _lock:
            if _active_prompt_id == pid:
                _active_prompt_id = None
        _stop_ws_relay(ws_proc)


atexit.register(shutdown_worker, 'Forge exit')
