"""Anthropic-compatible provider override (e.g. Xiaomi MiMo Token Plan):
ANTHROPIC_BASE_URL + LLM_MODEL flow into the planner client, the Pi launch
flags, and the sandbox env — and credentials NEVER enter a no-egress sandbox.
All offline."""
import json

import pytest

import src.sandbox.sandbox as sandbox_mod
from src import config
from src.executor_adapters.pi_adapter import pi_launch_args, pi_models_json
from src.sandbox.registry import get_workspace, reset_workspaces
from src.sandbox.sandbox import Sandbox
from src.state import new_state

MIMO_URL = "https://token-plan-cn.xiaomimimo.com/anthropic"


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert config.anthropic_base_url() is None
    assert config.llm_model() is None


def test_pi_launch_args_pin_provider_and_take_model(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert pi_launch_args() == ("--provider", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "mimo-v2.5-pro")
    assert pi_launch_args() == ("--provider", "anthropic",
                                "--model", "mimo-v2.5-pro")


def test_pi_models_json_overrides_provider_base_url(monkeypatch):
    """pi ignores the ANTHROPIC_BASE_URL env var (verified empirically); the
    working override is a models.json provider entry, written into the sandbox."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert pi_models_json() is None
    monkeypatch.setenv("ANTHROPIC_BASE_URL", MIMO_URL)
    assert json.loads(pi_models_json()) == {
        "providers": {"anthropic": {"baseUrl": MIMO_URL}}
    }


def test_sandbox_env_args_gate_credentials_on_egress(tmp_path):
    kwargs = dict(anthropic_api_key="tp-secret", anthropic_base_url=MIMO_URL)
    with_egress = Sandbox("t", "r", tmp_path, allow_egress=True, **kwargs)
    args = with_egress._env_args()
    assert "ANTHROPIC_API_KEY=tp-secret" in args
    assert f"ANTHROPIC_BASE_URL={MIMO_URL}" in args

    # verification-only sandbox: no egress → no secrets inside, ever
    no_egress = Sandbox("t", "r", tmp_path, allow_egress=False, **kwargs)
    assert not any("ANTHROPIC" in a for a in no_egress._env_args())

    # base URL without a key is meaningless — not injected alone
    url_only = Sandbox("t", "r", tmp_path, allow_egress=True,
                       anthropic_base_url=MIMO_URL)
    assert not any("ANTHROPIC" in a for a in url_only._env_args())


@pytest.mark.parametrize("adapter, egress, key", [
    ("pi", True, "tp-secret"),
    ("dryrun", False, None),  # imported-repo verification sandbox
])
def test_registry_builds_pi_sandboxes_with_credentials(monkeypatch, tmp_path,
                                                       adapter, egress, key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tp-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", MIMO_URL)
    built = {}

    class _Recorder:
        def __init__(self, *args, **kwargs):
            built.update(kwargs)

        def start(self):
            return self

    monkeypatch.setattr(sandbox_mod, "Sandbox", _Recorder)
    reset_workspaces()
    state = new_state(thread_id="r", tenant_id="t", repo_path="/workspace/t",
                      host_repo_path=str(tmp_path), executor_adapter=adapter,
                      workspace_kind="docker")
    get_workspace(state)
    reset_workspaces()
    assert built["allow_egress"] is egress
    assert built["anthropic_api_key"] == key
    assert built["anthropic_base_url"] == (MIMO_URL if egress else None)
