NAME=$(shell basename `pwd`)
ARGO_TARGET_NAMESPACE=manuela-ci
PATTERN=industrial-edge
COMPONENT=datacenter
SECRET_NAME="argocd-env"
TARGET_BRANCH=$(shell git rev-parse --abbrev-ref HEAD)
HUBCLUSTER_APPS_DOMAIN=$(shell oc get ingresses.config/cluster -o jsonpath={.spec.domain})
TARGET_ORIGIN ?= origin
TARGET_REPO=$(shell git ls-remote --get-url --symref $(TARGET_ORIGIN) | sed -e 's/.*URL:[[:space:]]*//' -e 's%^git@%%' -e 's%^https://%%' -e 's%:%/%' -e 's%^%https://%')
CHART_OPTS=-f common/examples/values-secret.yaml -f values-global.yaml -f values-datacenter.yaml --set global.targetRevision=main --set global.valuesDirectoryURL="https://github.com/pattern-clone/pattern/raw/main/" --set global.pattern="industrial-edge" --set global.namespace="pattern-namespace"
HELM_OPTS=-f values-global.yaml --set main.git.repoURL="$(TARGET_REPO)" --set main.git.revision=$(TARGET_BRANCH) --set global.hubClusterDomain=$(HUBCLUSTER_APPS_DOMAIN)

.PHONY: default
default: show

.PHONY: help
# No need to add a comment here as help is described in common/
##@ Pattern tasks

help:
	@make -f common/Makefile MAKEFILE_LIST="Makefile common/Makefile" help

%:
	make -f common/Makefile $*

pipeline-setup: ## calls the helm pipeline-setup
	helm upgrade --install $(NAME)-secrets charts/secrets/pipeline-setup $(HELM_OPTS)

install: pipeline-setup operator-deploy post-install ## installs the pattern, inits the vault and loads the secrets
	@echo "Installed"

post-install: ## Post-install tasks
	make load-secrets
	make argosecret
	@echo "Done"

sleep: ## waits for all seed resources to be presents
	scripts/sleep-seed.sh

sleep-seed: sleep seed ## waits for seed resources and calls seed-run
	true

seed: sleep ## waits for all seed resources
	oc create -f charts/datacenter/pipelines/extra/seed-run.yaml

#  Makefiles that use this target must provide:
#  	PATTERN: The name of the pattern that is using it.  This will be used programmatically for the source namespace
#  	TARGET_NAMESPACE: target namespace to install the secret into
#  	COMPONENT: The component of the target namespace.  In industrial edge, factory or datacenter - and for the secret
#  		it needs to be datacenter because that's where the CI components run.
#  	SECRET_NAME: The name of the secret to manage
argosecret: ## creates the argo secret
	PATTERN="$(PATTERN)" TARGET_NAMESPACE="$(ARGO_TARGET_NAMESPACE)" COMPONENT="$(COMPONENT)" SECRET_NAME="$(SECRET_NAME)" scripts/secret.sh

build-and-test: ## run a build and test pipeline
	oc create -f charts/datacenter/pipelines/extra/build-and-test-run.yaml

just-pr: ## run a build and test pipeline
	oc create -f charts/datacenter/pipelines/extra/just-pr-run.yaml

build-and-test-iot-anomaly-detection: ## run a build and test pipeline iot anomaly detection
	oc create -f charts/datacenter/pipelines/extra/build-and-test-run-iot-anomaly-detection.yaml

build-and-test-iot-consumer: ## run a build and test pipeline iot consumer
	oc create -f charts/datacenter/pipelines/extra/build-and-test-run-iot-consumer.yaml

# We only check the remote ssh git branch's existance if we're not running inside a container
# as getting ssh auth working inside a container seems a bit brittle
# If the main repoUpstreamURL field is set, then we need to check against
# that and not target_repo
.PHONY: validate-origin
validate-origin: ## verify the git origin is available
	@echo "Checking repository:"
	$(eval UPSTREAMURL := $(shell yq -r '.main.git.repoUpstreamURL // (.main.git.repoUpstreamURL = "")' values-global.yaml))
	@if [ -z "$(UPSTREAMURL)" ]; then\
		echo -n "  $(TARGET_REPO) - branch '$(TARGET_BRANCH)': ";\
		git ls-remote --exit-code --heads $(TARGET_REPO) $(TARGET_BRANCH) >/dev/null &&\
			echo "OK" || (echo "NOT FOUND"; exit 1);\
	else\
		echo "Upstream URL set to: $(UPSTREAMURL)";\
		echo -n "  $(UPSTREAMURL) - branch '$(TARGET_BRANCH)': ";\
		git ls-remote --exit-code --heads $(UPSTREAMURL) $(TARGET_BRANCH) >/dev/null &&\
			echo "OK" || (echo "NOT FOUND"; exit 1);\
	fi

.PHONY: validate-cluster
validate-cluster: ## Do some cluster validations before installing
	@echo "Checking cluster:"
	@echo -n "  cluster-info: "
	@oc cluster-info >/dev/null && echo "OK" || (echo "Error"; exit 1)
	@echo -n "  storageclass: "
	@if [ `oc get storageclass -o go-template='{{printf "%d\n" (len .items)}}'` -eq 0 ]; then\
		echo "WARNING: No storageclass found";\
	else\
		echo "OK";\
	fi


.PHONY: validate-schema
validate-schema: ## validates values files against schema in common/clustergroup
	$(eval VAL_PARAMS := $(shell for i in ./values-*.yaml; do echo -n "$${i} "; done))
	@echo -n "Validating clustergroup schema of: "
	@set -e; for i in $(VAL_PARAMS); do echo -n " $$i"; helm template common/clustergroup $(HELM_OPTS) -f "$${i}" >/dev/null; done
	@echo

.PHONY: validate-prereq
validate-prereq: ## verify pre-requisites
	@if [ ! -f /run/.containerenv ]; then\
	  echo "Checking prerequisites:";\
	  for t in $(EXECUTABLES); do if ! which $$t > /dev/null 2>&1; then echo "No $$t in PATH"; exit 1; fi; done;\
	  echo "  Check for '$(EXECUTABLES)': OK";\
	  echo -n "  Check for python-kubernetes: ";\
	  if ! ansible -m ansible.builtin.command -a "{{ ansible_python_interpreter }} -c 'import kubernetes'" localhost > /dev/null 2>&1; then echo "Not found"; exit 1; fi;\
	  echo "OK";\
	  echo -n "  Check for kubernetes.core collection: ";\
	  if ! ansible-galaxy collection list | grep kubernetes.core > /dev/null 2>&1; then echo "Not found"; exit 1; fi;\
	  echo "OK";\
	else\
		if [ -f values-global.yaml ]; then\
			OUT=`yq -r '.main.multiSourceConfig.enabled // (.main.multiSourceConfig.enabled = "false")' values-global.yaml`;\
			if [ "$${OUT,,}" = "false" ]; then\
				echo "You must set \".main.multiSourceConfig.enabled: true\" in your 'values-global.yaml' file";\
				echo "because your common subfolder is the slimmed down version with no helm charts in it";\
				exit 1;\
			fi;\
		fi;\
	fi

.PHONY: argo-healthcheck
argo-healthcheck: ## Checks if all argo applications are synced
	@echo "Checking argo applications"
	$(eval APPS := $(shell oc get applications -A -o jsonpath='{range .items[*]}{@.metadata.namespace}{","}{@.metadata.name}{"\n"}{end}'))
	@NOTOK=0; \
	for i in $(APPS); do\
		n=`echo "$${i}" | cut -f1 -d,`;\
		a=`echo "$${i}" | cut -f2 -d,`;\
		STATUS=`oc get -n "$${n}" application/"$${a}" -o jsonpath='{.status.sync.status}'`;\
		if [[ $$STATUS != "Synced" ]]; then\
			NOTOK=$$(( $${NOTOK} + 1));\
		fi;\
		HEALTH=`oc get -n "$${n}" application/"$${a}" -o jsonpath='{.status.health.status}'`;\
		if [[ $$HEALTH != "Healthy" ]]; then\
			NOTOK=$$(( $${NOTOK} + 1));\
		fi;\
		echo "$${n} $${a} -> Sync: $${STATUS} - Health: $${HEALTH}";\
	done;\
	if [ $${NOTOK} -gt 0 ]; then\
	    echo "Some applications are not synced or are unhealthy";\
	    exit 1;\
	fi


##@ Test and Linters Tasks

.PHONY: qe-tests
qe-tests: ## Runs the tests that QE runs
	@set -e; if [ -f ./tests/interop/run_tests.sh ]; then \
		./tests/interop/run_tests.sh; \
	else \
		echo "No ./tests/interop/run_tests.sh found skipping"; \
	fi

.PHONY: super-linter
super-linter: ## Runs super linter locally
	rm -rf .mypy_cache
	podman run -e RUN_LOCAL=true -e USE_FIND_ALGORITHM=true	\
					-e VALIDATE_ANSIBLE=false \
					-e VALIDATE_BASH=false \
					-e VALIDATE_CHECKOV=false \
					-e VALIDATE_DOCKERFILE_HADOLINT=false \
					-e VALIDATE_JSCPD=false \
					-e VALIDATE_JSON_PRETTIER=false \
					-e VALIDATE_MARKDOWN_PRETTIER=false \
					-e VALIDATE_KUBERNETES_KUBECONFORM=false \
					-e VALIDATE_PYTHON_PYLINT=false \
					-e VALIDATE_SHELL_SHFMT=false \
					-e VALIDATE_TEKTON=false \
					-e VALIDATE_YAML=false \
					-e VALIDATE_YAML_PRETTIER=false \
					$(DISABLE_LINTERS) \
					-v $(PWD):/tmp/lint:rw,z \
					-w /tmp/lint \
					ghcr.io/super-linter/super-linter:slim-v7

.PHONY: deploy upgrade legacy-deploy legacy-upgrade
deploy upgrade legacy-deploy legacy-upgrade:
	@echo "UNSUPPORTED TARGET: please switch to 'operator-deploy'"; exit 1
