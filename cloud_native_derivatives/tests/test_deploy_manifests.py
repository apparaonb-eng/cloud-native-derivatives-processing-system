"""Sanity checks on the deployment artefacts."""
import glob
import yaml


def _docs():
    for f in sorted(glob.glob("k8s/*.yaml")):
        with open(f) as fh:
            for d in yaml.safe_load_all(fh):
                if d:
                    yield f, d


def test_manifests_parse_and_deployments_have_probes_and_limits():
    kinds = {}
    for f, d in _docs():
        kinds.setdefault(d["kind"], []).append(d)
        if d["kind"] == "Deployment":
            c = d["spec"]["template"]["spec"]["containers"][0]
            assert "readinessProbe" in c and "livenessProbe" in c, f
            assert "limits" in c["resources"] and "requests" in c["resources"], f
    assert len(kinds["Deployment"]) == 4 and "HorizontalPodAutoscaler" in kinds


def test_compose_parses():
    c = yaml.safe_load(open("docker-compose.yml"))
    assert {"redis", "marketdata", "pricing", "portfolio"} <= set(c["services"])
