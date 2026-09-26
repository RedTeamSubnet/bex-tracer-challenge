# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
#
# BUILD CONTEXT IS THE REPO ROOT, not this directory. `extensions.yml` and
# `scripts/fetch_extensions.py` live at the root and the image needs both, so
# compose.yml sets `context: .` and points `dockerfile:` here. Every COPY below
# is therefore written relative to the repo root.
#
# PLATFORM: linux/amd64 only. Chrome for Testing ships no linux-arm64 build
# before 153, and 153 is Beta - a scoring challenge does not run Beta Chrome.
# compose.yml pins the platform; on Apple Silicon that means a QEMU build.

ARG PYTHON_VERSION=3.10
ARG BASE_IMAGE=python:${PYTHON_VERSION}-slim-trixie

ARG DEBIAN_FRONTEND=noninteractive
ARG EXC_CHALLENGE_API_SLUG="rest-exc-challenge"

## Keep in lockstep with `chrome_for_testing_version` in extensions.yml: that
## value is sent as the CRX `prodversion`, so a mismatch means the extensions
## were fetched for a different Chrome than the one that will load them.
ARG CFT_VERSION="152.0.7977.54"
ARG CFT_CHROME_SHA256="88af83664e1e5f79dc1c1378d0699b98dddd69690a748addf4ccbe322bfacedf"
ARG CFT_DRIVER_SHA256="66f3984d666de8455706de8968bd7afb6ad3fa70c1a08084bba8235e146617a6"


## Here is the Chrome-for-Testing image:
FROM ${BASE_IMAGE} AS chrome

ARG DEBIAN_FRONTEND
ARG CFT_VERSION
ARG CFT_CHROME_SHA256
ARG CFT_DRIVER_SHA256

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Hashes are verified before anything is unpacked. A silent swap of the browser
# binary would change every label the challenge produces.
RUN	apt-get update && \
	apt-get install -y --no-install-recommends ca-certificates curl unzip && \
	_CFT_BASE="https://storage.googleapis.com/chrome-for-testing-public/${CFT_VERSION}/linux64" && \
	curl -fsSL -o /tmp/chrome.zip "${_CFT_BASE}/chrome-linux64.zip" && \
	curl -fsSL -o /tmp/driver.zip "${_CFT_BASE}/chromedriver-linux64.zip" && \
	echo "${CFT_CHROME_SHA256}  /tmp/chrome.zip" | sha256sum -c - && \
	echo "${CFT_DRIVER_SHA256}  /tmp/driver.zip" | sha256sum -c - && \
	mkdir -p /opt/chrome && \
	unzip -q /tmp/chrome.zip -d /tmp/x && mv /tmp/x/chrome-linux64 /opt/chrome/browser && \
	unzip -q /tmp/driver.zip -d /tmp/y && mv /tmp/y/chromedriver-linux64 /opt/chrome/driver && \
	chmod -R a+rX /opt/chrome && \
	chmod a+x /opt/chrome/browser/chrome /opt/chrome/driver/chromedriver && \
	rm -rf /tmp/chrome.zip /tmp/driver.zip /tmp/x /tmp/y


## Here is the extension image:
## Downloads, sha-verifies, CRX3-parses and unpacks every pinned extension, and
## injects each `key` so Chrome derives the real store id instead of a
## path-derived one. Needs network at build; prod runs with egress blocked.
FROM ${BASE_IMAGE} AS extensions

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN --mount=type=cache,target=/root/.cache,sharing=locked \
	python3 -m pip install --timeout 60 -q PyYAML

RUN	--mount=type=bind,source=src/exc_challenge/challenge/extensions.yml,target=/tmp/extensions.yml \
	--mount=type=bind,source=src/exc_challenge/challenge/extensions.lock.yml,target=/tmp/extensions.lock.yml \
	--mount=type=bind,source=scripts/fetch_extensions.py,target=/tmp/fetch_extensions.py \
	python3 /tmp/fetch_extensions.py --pool /tmp/extensions.yml --out /opt/extensions && \
	chmod -R a+rX /opt/extensions


## Here is the builder image:
FROM ${BASE_IMAGE} AS builder

ARG DEBIAN_FRONTEND
ARG EXC_CHALLENGE_API_SLUG

ENV	UV_LINK_MODE=copy

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR "/usr/src/${EXC_CHALLENGE_API_SLUG}"

RUN --mount=type=cache,target=/root/.cache,sharing=locked \
	_BUILD_TARGET_ARCH=$(uname -m) && \
	echo "BUILDING TARGET ARCHITECTURE: ${_BUILD_TARGET_ARCH}" && \
	rm -rfv /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* && \
	python3 -m pip install --timeout 60 -U pip uv

RUN	--mount=type=cache,target=/root/.cache,sharing=locked \
	--mount=type=bind,source=src/exc_challenge/challenge/requirements.txt,target=requirements.txt \
	python3 -m uv pip install --prefix=/install -r ./requirements.txt


## Here is the base image:
FROM ${BASE_IMAGE} AS base

ARG DEBIAN_FRONTEND
ARG EXC_CHALLENGE_API_SLUG

ARG EXC_CHALLENGE_HOME_DIR="/app"
ARG EXC_CHALLENGE_API_DIR="${EXC_CHALLENGE_HOME_DIR}/${EXC_CHALLENGE_API_SLUG}"
ARG EXC_CHALLENGE_API_CONFIGS_DIR="/etc/${EXC_CHALLENGE_API_SLUG}"
ARG EXC_CHALLENGE_API_DATA_DIR="/var/lib/${EXC_CHALLENGE_API_SLUG}"
ARG EXC_CHALLENGE_API_LOGS_DIR="/var/log/${EXC_CHALLENGE_API_SLUG}"
ARG EXC_CHALLENGE_API_TMP_DIR="/tmp/${EXC_CHALLENGE_API_SLUG}"
ARG EXC_CHALLENGE_API_PORT=10001
ARG EXC_CHALLENGE_API_DOCS_ENABLED=true
## Per-round Chrome profiles and extension copies. compose mounts a tmpfs here;
## Chrome rewrites `_metadata/` on every unpacked load, so it cannot be read-only.
ARG EXC_CHALLENGE_SCRATCH_DIR="/run/exc"
## echo "EXC_CHALLENGE_USER_PASSWORD123" | openssl passwd -6 -stdin
ARG HASH_PASSWORD="\$6\$Ex9TNhjjLMtBMG.j\$X4psLehOPZ2Q0GOnzqjShmzcUg4SvjNV663a6wBQA78/sk5JjDzDPOurHBjPCTem2IwM4lbO/c1jkUJYMouOu/"
ARG UID=1000
ARG GID=11000
ARG USER=EXC-user
ARG GROUP=EXC-group

ENV EXC_CHALLENGE_API_SLUG="${EXC_CHALLENGE_API_SLUG}" \
	EXC_CHALLENGE_HOME_DIR="${EXC_CHALLENGE_HOME_DIR}" \
	EXC_CHALLENGE_API_DIR="${EXC_CHALLENGE_API_DIR}" \
	EXC_CHALLENGE_API_CONFIGS_DIR="${EXC_CHALLENGE_API_CONFIGS_DIR}" \
	EXC_CHALLENGE_API_DATA_DIR="${EXC_CHALLENGE_API_DATA_DIR}" \
	EXC_CHALLENGE_API_LOGS_DIR="${EXC_CHALLENGE_API_LOGS_DIR}" \
	EXC_CHALLENGE_API_TMP_DIR="${EXC_CHALLENGE_API_TMP_DIR}" \
	EXC_CHALLENGE_API_PORT=${EXC_CHALLENGE_API_PORT} \
	EXC_CHALLENGE_API_DOCS_ENABLED=${EXC_CHALLENGE_API_DOCS_ENABLED} \
	EXC_CHALLENGE_CHALLENGE_BROWSER_SCRATCH_DIR="${EXC_CHALLENGE_SCRATCH_DIR}" \
	UID=${UID} \
	GID=${GID} \
	USER=${USER} \
	GROUP=${GROUP} \
	PYTHONIOENCODING=utf-8 \
	PYTHONUNBUFFERED=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN --mount=type=secret,id=HASH_PASSWORD \
	rm -vrf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /root/.cache/* && \
	apt-get clean -y && \
	apt-get update --fix-missing -o Acquire::CompressionTypes::Order::=gz && \
	apt-get install -y --no-install-recommends \
		sudo \
		gosu \
		tini \
		locales \
		tzdata \
		procps \
		iputils-ping \
		iproute2 \
		curl \
		nano \
		# --- Chrome runtime libraries -----------------------------------
		# Debian trixie completed the 64-bit time_t transition, so these are
		# the `t64` names. A bookworm dependency list fails here, which is why
		# the `ldd` gate in the app stage exists.
		ca-certificates \
		fonts-liberation \
		libasound2t64 \
		libatk-bridge2.0-0t64 \
		libatk1.0-0t64 \
		libatspi2.0-0t64 \
		libcairo2 \
		libcups2t64 \
		libdbus-1-3 \
		libdrm2 \
		libgbm1 \
		libglib2.0-0t64 \
		libgtk-3-0t64 \
		libnspr4 \
		libnss3 \
		libpango-1.0-0 \
		libvulkan1 \
		libx11-6 \
		libxcb1 \
		libxcomposite1 \
		libxdamage1 \
		libxext6 \
		libxfixes3 \
		libxkbcommon0 \
		libxrandr2 \
		libxrender1 \
		libxtst6 \
		# Installed but NEVER started in prod - see the note in compose.yml.
		# Running Xvfb changes Chrome's code path and invalidates the
		# headless/headful equivalence the labels depend on.
		xvfb && \
	apt-get clean -y && \
	sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
	sed -i -e 's/# en_AU.UTF-8 UTF-8/en_AU.UTF-8 UTF-8/' /etc/locale.gen && \
	dpkg-reconfigure --frontend=noninteractive locales && \
	update-locale LANG=en_US.UTF-8 && \
	echo "LANGUAGE=en_US.UTF-8" >> /etc/default/locale && \
	echo "LC_ALL=en_US.UTF-8" >> /etc/default/locale && \
	addgroup --gid ${GID} ${GROUP} && \
	useradd -lmN -d "/home/${USER}" -s /bin/bash -g ${GROUP} -G sudo -u ${UID} ${USER} && \
	if [ -f "/run/secrets/HASH_PASSWORD" ]; then \
		echo "Using hashed password from secret: /run/secrets/HASH_PASSWORD"; \
		echo -e "${USER}:$(cat /run/secrets/HASH_PASSWORD)" | chpasswd -e; \
	else \
		echo "Using hashed password from build argument: HASH_PASSWORD"; \
		echo -e "${USER}:${HASH_PASSWORD}" | chpasswd -e; \
	fi && \
	echo -e "\nalias ls='ls -aF --group-directories-first --color=auto'" >> /root/.bashrc && \
	echo -e "alias ll='ls -alhF --group-directories-first --color=auto'\n" >> /root/.bashrc && \
	echo -e "\numask 0002" >> "/home/${USER}/.bashrc" && \
	echo "alias ls='ls -aF --group-directories-first --color=auto'" >> "/home/${USER}/.bashrc" && \
	echo -e "alias ll='ls -alhF --group-directories-first --color=auto'\n" >> "/home/${USER}/.bashrc" && \
	rm -rfv /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/* /root/.cache/* "/home/${USER}/.cache/*" && \
	mkdir -pv "${EXC_CHALLENGE_API_DIR}" \
		"${EXC_CHALLENGE_API_CONFIGS_DIR}" \
		"${EXC_CHALLENGE_API_DATA_DIR}" \
		"${EXC_CHALLENGE_API_LOGS_DIR}" \
		"${EXC_CHALLENGE_API_TMP_DIR}" \
		"${EXC_CHALLENGE_SCRATCH_DIR}" && \
	chown -Rc "${USER}:${GROUP}" \
		"${EXC_CHALLENGE_HOME_DIR}" \
		"${EXC_CHALLENGE_API_CONFIGS_DIR}" \
		"${EXC_CHALLENGE_API_DATA_DIR}" \
		"${EXC_CHALLENGE_API_LOGS_DIR}" \
		"${EXC_CHALLENGE_API_TMP_DIR}" \
		"${EXC_CHALLENGE_SCRATCH_DIR}" && \
	find "${EXC_CHALLENGE_API_DIR}" "${EXC_CHALLENGE_API_CONFIGS_DIR}" "${EXC_CHALLENGE_API_DATA_DIR}" -type d -exec chmod -c 770 {} + && \
	find "${EXC_CHALLENGE_API_DIR}" "${EXC_CHALLENGE_API_CONFIGS_DIR}" "${EXC_CHALLENGE_API_DATA_DIR}" -type d -exec chmod -c ug+s {} + && \
	find "${EXC_CHALLENGE_API_LOGS_DIR}" "${EXC_CHALLENGE_API_TMP_DIR}" "${EXC_CHALLENGE_SCRATCH_DIR}" -type d -exec chmod -c 775 {} + && \
	find "${EXC_CHALLENGE_API_LOGS_DIR}" "${EXC_CHALLENGE_API_TMP_DIR}" -type d -exec chmod -c +s {} +

ENV LANG=en_US.UTF-8 \
	LANGUAGE=en_US.UTF-8 \
	LC_ALL=en_US.UTF-8

COPY --from=builder --chown=${UID}:${GID} /install /usr/local


## Here is the final image:
FROM base AS app

ARG UID
ARG GID
ARG CFT_VERSION

COPY --from=chrome --chown=${UID}:${GID} /opt/chrome /opt/chrome
COPY --from=extensions --chown=${UID}:${GID} /opt/extensions /opt/extensions

WORKDIR "${EXC_CHALLENGE_API_DIR}"
COPY --chown=${UID}:${GID} ./src/exc_challenge/challenge/api ${EXC_CHALLENGE_API_DIR}/api
## The bait page. `api/mount.py` resolves it as <api_dir>/../templates, so this
## path is load-bearing: StaticFiles raises at import time if it is missing and
## the app never starts.
COPY --chown=${UID}:${GID} ./src/exc_challenge/challenge/templates ${EXC_CHALLENGE_API_DIR}/templates
## `config.challenge.pool_path` defaults to this path. It sits beside api/ and
## templates/ rather than loose in ${EXC_CHALLENGE_HOME_DIR}, matching the package layout.
COPY --chown=${UID}:${GID} ./src/exc_challenge/challenge/extensions.yml ${EXC_CHALLENGE_API_DIR}/extensions.yml
## Store ids and pins. Private - not in the public repository, only here.
COPY --chown=${UID}:${GID} ./src/exc_challenge/challenge/extensions.lock.yml ${EXC_CHALLENGE_API_DIR}/extensions.lock.yml
COPY --chown=${UID}:${GID} --chmod=770 ./src/exc_challenge/challenge/scripts/*.sh /usr/local/bin/
COPY --chown=${UID}:${GID} --chmod=775 ./src/exc_challenge/challenge/scripts/run_round.py /usr/local/bin/

## Build-time gates. These fail the IMAGE rather than the first /score call:
## a broken Chrome otherwise scores every miner 0.0 and looks like it works.
RUN	--mount=type=bind,source=scripts/verify_chrome_build.py,target=/tmp/verify_chrome_build.py \
	python3 /tmp/verify_chrome_build.py --expect-version "${CFT_VERSION}"

# VOLUME ["${EXC_CHALLENGE_API_DATA_DIR}"]
# EXPOSE ${EXC_CHALLENGE_API_PORT}

# USER ${UID}:${GID}
# HEALTHCHECK --start-period=30s --start-interval=1s --interval=5m --timeout=5s --retries=3 \
# 	CMD curl -f http://localhost:${EXC_CHALLENGE_API_PORT}/ping || exit 1

## tini as PID 1: Chrome forks a zygote process and orphans grandchildren on
## every round. Without a reaper those accumulate as zombies until the container
## hits pids_limit and rounds start failing for no visible reason.
ENTRYPOINT ["/usr/bin/tini", "--", "docker-entrypoint.sh"]
# CMD ["-b", "uvicorn api.main:app --host=0.0.0.0 --port=${EXC_CHALLENGE_API_PORT:-10001} --no-access-log --no-server-header --proxy-headers --forwarded-allow-ips='*'"]
