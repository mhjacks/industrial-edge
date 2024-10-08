import logging
import os

import pytest
import yaml
from ocp_resources.storage_class import StorageClass
from validatedpatterns_tests.interop import application, components
from validatedpatterns_tests.interop.crd import ManagedCluster

from . import __loggername__

logger = logging.getLogger(__loggername__)

oc = os.environ["HOME"] + "/oc_client/oc"

"""
Validate following manuela components pods and endpoints on
hub site (central server):

1) ACM (Advanced Cluster Manager) and self-registration
2) argocd
3) openshift-pipeline
4) manuela-tsts-all
    a) kafka cluster
    b) amq stream and broker
    c) application
        1) machine sensors
        2) messaging
        3) line dashboard
        4) anomaly-detection-predictor
    d) seldon controller
    e) camel-k operator
    f) mqtt2kafka integration
4) manuela-ci
5) manuela-data-lake-central-kafka-cluster
    a) Kafka cluster
    b) amq stream operator
6) manuela-data-lake-central-s3-store
    a) kafka to s3 integration
    b) camel-k operator
7) opendatahub pods and endpoints
8) applications health (Applications deployed through argocd)
"""


@pytest.mark.test_validate_hub_site_components
def test_validate_hub_site_components(openshift_dyn_client):
    logger.info("Checking Openshift version on hub site")
    version_out = components.dump_openshift_version()
    logger.info(f"Openshift version:\n{version_out}")

    logger.info("Dump PVC and storageclass info")
    pvcs_out = components.dump_pvc()
    logger.info(f"PVCs:\n{pvcs_out}")

    for sc in StorageClass.get(dyn_client=openshift_dyn_client):
        logger.info(sc.instance)


@pytest.mark.validate_hub_site_reachable
def test_validate_hub_site_reachable(kube_config, openshift_dyn_client):
    logger.info("Check if hub site API end point is reachable")
    namespace = "openshift-gitops"
    sub_string = "argocd-dex-server-token"
    try:
        hub_api_url = application.get_site_api_url(kube_config)
        hub_api_response = application.get_site_api_response(
            openshift_dyn_client, hub_api_url, namespace, sub_string
        )
    except AssertionError as e:
        logger.error(f"FAIL: {e}")
        assert False, e

    if hub_api_response.status_code != 200:
        err_msg = "Hub site is not reachable. Please check the deployment."
        logger.error(f"FAIL: {err_msg}")
        assert False, err_msg
    else:
        logger.info("PASS: Hub site is reachable")


@pytest.mark.check_pod_status_hub
def test_check_pod_status(openshift_dyn_client):
    logger.info("Checking pod status")

    err_msg = []
    failed_pods = []
    missing_pods = []
    missing_projects = []
    projects = [
        "openshift-operators",
        "open-cluster-management",
        "open-cluster-management-hub",
        "openshift-gitops",
        "industrial-edge-datacenter",
        "manuela-tst-all",
        "openshift-pipelines",
        "manuela-ci",
        "manuela-data-lake",
        "vault",
    ]

    missing_projects = components.check_project_absense(openshift_dyn_client, projects)

    for project in projects:
        logger.info(f"Checking pods in namespace '{project}'")
        missing_pods += components.check_pod_absence(openshift_dyn_client, project)
        failed_pods += components.check_pod_status(openshift_dyn_client, projects)

    if missing_projects:
        err_msg.append(f"The following namespaces are missing: {missing_projects}")

    if missing_pods:
        err_msg.append(
            f"The following namespaces have no pods deployed: {missing_pods}"
        )

    if failed_pods:
        err_msg.append(f"The following pods are failed: {failed_pods}")

    if err_msg:
        logger.error(f"FAIL: {err_msg}")
        assert False, err_msg
    else:
        logger.info("PASS: Pod status check succeeded.")


# No longer needed for ACM 2.7
#
# @pytest.mark.validate_acm_route_reachable
# def test_validate_acm_route_reachable(openshift_dyn_client):
#     namespace = "open-cluster-management"

#     logger.info("Check if ACM route is reachable")
#     try:
#         for route in Route.get(
#             dyn_client=openshift_dyn_client,
#             namespace=namespace,
#             name="multicloud-console",
#         ):
#             acm_route_url = route.instance.spec.host
#     except NotFoundError:
#         err_msg = (
#             "ACM url/route is missing in open-cluster-management namespace"
#         )
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg

#     final_acm_url = f"{'http://'}{acm_route_url}"
#     logger.info(f"ACM route/url : {final_acm_url}")

#     bearer_token = get_long_live_bearer_token(
#         dyn_client=openshift_dyn_client,
#         namespace=namespace,
#         sub_string="multiclusterhub-operator-token",
#     )
#     if not bearer_token:
#         err_msg = (
#             "Bearer token is missing for ACM in open-cluster-management"
#             " namespace"
#         )
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg
#     else:
#         logger.debug(f"ACM bearer token : {bearer_token}")

#     acm_route_response = get_site_response(
#         site_url=final_acm_url, bearer_token=bearer_token
#     )

#     logger.info(f"ACM route response : {acm_route_response}")

#     if acm_route_response.status_code != 200:
#         err_msg = "ACM is not reachable. Please check the deployment"
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg
#     else:
#         logger.info("PASS: ACM is reachable.")


@pytest.mark.validate_acm_self_registration_managed_clusters
def test_validate_acm_self_registration_managed_clusters(openshift_dyn_client):
    logger.info("Check ACM self registration for edge site")

    kubefile = os.getenv("KUBECONFIG_EDGE")
    kubefile_exp = os.path.expandvars(kubefile)
    with open(kubefile_exp) as stream:
        try:
            out = yaml.safe_load(stream)
            site_name = out["clusters"][0]["name"]
        except yaml.YAMLError:
            err_msg = "Failed to load kubeconfig file"
            logger.error(f"FAIL: {err_msg}")
            assert False, err_msg

    clusters = ManagedCluster.get(dyn_client=openshift_dyn_client, name=site_name)
    cluster = next(clusters)
    is_managed_cluster_joined, managed_cluster_status = cluster.self_registered

    logger.info(f"Cluster Managed : {is_managed_cluster_joined}")
    logger.info(f"Managed Cluster Status : {managed_cluster_status}")

    if not is_managed_cluster_joined:
        err_msg = f"{site_name} is not self registered"
        logger.error(f"FAIL: {err_msg}")
        assert False, err_msg
    else:
        logger.info(f"PASS: {site_name} is self registered")


@pytest.mark.validate_argocd_reachable_hub_site
def test_validate_argocd_reachable_hub_site(openshift_dyn_client):
    namespace = "openshift-gitops"
    name = "openshift-gitops-server"
    sub_string = "argocd-dex-server-token"
    logger.info("Check if argocd route/url on hub site is reachable")
    try:
        argocd_route_url = application.get_argocd_route_url(
            openshift_dyn_client, namespace, name
        )
        argocd_route_response = application.get_site_api_response(
            openshift_dyn_client, argocd_route_url, namespace, sub_string
        )
    except StopIteration:
        err_msg = "Argocd url/route is missing in open-cluster-management namespace"
        logger.error(f"FAIL: {err_msg}")
        assert False, err_msg
    except AssertionError:
        err_msg = "Bearer token is missing for argocd-dex-server"
        logger.error(f"FAIL: {err_msg}")
        assert False, err_msg

    logger.info(f"Argocd route response : {argocd_route_response}")

    if argocd_route_response.status_code != 200:
        err_msg = "Argocd is not reachable. Please check the deployment"
        logger.error(f"FAIL: {err_msg}")
        assert False, err_msg
    else:
        logger.info("PASS: Argocd is reachable")


# @pytest.mark.skip
# @pytest.mark.validate_jupyterhub_pods_hub_site
# def test_validate_jupyterhub_pods_hub_site(openshift_dyn_client):
#     pods_down = 0
#     namespace = "manuela-ml-workspace"
#     logger.info(
#         "Check if jupyterhub pods are in 'Running' state in" f" {namespace} namespace"
#     )

#     try:
#         jupyterhub_pods = Pod.get(
#             dyn_client=openshift_dyn_client,
#             namespace=namespace,
#             label_selector="deploymentconfig=jupyterhub",
#         )
#         pod = next(jupyterhub_pods)
#     except StopIteration:
#         err_msg = f"There are no jupyterhub pods in {namespace} namespace"
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg

#     for pod in jupyterhub_pods:
#         container = pod.instance.status.containerStatuses[0]
#         podname, containername = pod.instance.metadata.name, container.name
#         logger.info(f"{podname}: {containername}: Ready: {container.ready}")

#         # The jupyterhub container for 2 of the 3 jupyterhub pods should
#         # show 'Waiting to become leader' status.
#         # One jupyterhub pod should show all containers ready.
#         if container.ready is not True:
#             log_out = subprocess.run(
#                 [
#                     oc,
#                     "logs",
#                     "-n",
#                     "manuela-ml-workspace",
#                     "-c",
#                     containername,
#                     podname,
#                 ],
#                 capture_output=True,
#             )
#             log_out = log_out.stdout.decode("utf-8")
#             logger.info(f"Log for container '{containername}' in pod '{podname}':")
#             logger.info(log_out)
#             pods_down += 1

#     if int(pods_down) > 2:
#         err_msg = "All jupyterhub pods deployed in manuela-ml-workspace failed"
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg
#     else:
#         logger.info(
#             "PASS: All jupyterhub pods deployed in manuela-ml-workspace"
#             " namespace are up."
#         )


# @pytest.mark.skip
# @pytest.mark.validate_jupyter_route_reachable
# def test_validate_jupyter_route_reachable(openshift_dyn_client):
#     namespace = "manuela-ml-workspace"

#     logger.info("Check if jupyterhub route/url on hub site is reachable")
#     try:
#         for route in Route.get(
#             dyn_client=openshift_dyn_client,
#             namespace=namespace,
#             name="jupyterhub",
#         ):
#             jupyterhub_route_url = route.instance.spec.host
#     except NotFoundError:
#         err_msg = "Jupyterhub url/route is missing in manuela-ml-workspace namespace"
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg

#     final_jupyterhub_url = f"{'http://'}{jupyterhub_route_url}"
#     logger.info(f"Jupyterhub route/url : {final_jupyterhub_url}")

#     bearer_token = get_long_live_bearer_token(
#         dyn_client=openshift_dyn_client,
#         namespace=namespace,
#         sub_string="jupyterhub-hub-token",
#     )

#     if not bearer_token:
#         err_msg = (
#             "Bearer token is missing for jupyterhub in manuela-ml-workspace"
#             " namespace"
#         )
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg
#     else:
#         logger.debug(f"Jupyterhub bearer token : {bearer_token}")

#     jupyterhub_route_response = get_site_response(
#         site_url=final_jupyterhub_url, bearer_token=bearer_token
#     )

#     logger.info(f"Jupyterhub route response : {jupyterhub_route_response}")

#     if jupyterhub_route_response.status_code != 200:
#         err_msg = "Jupyterhub is not reachable. Please check the deployment"
#         logger.error(f"FAIL: {err_msg}")
#         assert False, err_msg
#     else:
#         logger.info("PASS: Jupyterhub is reachable.")


@pytest.mark.validate_argocd_applications_health_hub_site
def test_validate_argocd_applications_health_hub_site(openshift_dyn_client):
    unhealthy_apps = []
    logger.info("Get all applications deployed by argocd on hub site")
    projects = ["openshift-gitops", "industrial-edge-datacenter"]
    for project in projects:
        unhealthy_apps += application.get_argocd_application_status(
            openshift_dyn_client, project
        )
    if unhealthy_apps:
        err_msg = "Some or all applications deployed on hub site are unhealthy"
        logger.error(f"FAIL: {err_msg}:\n{unhealthy_apps}")
        assert False, err_msg
    else:
        logger.info("PASS: All applications deployed on hub site are healthy.")
