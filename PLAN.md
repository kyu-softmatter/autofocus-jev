# autofocus_Jev 개발 계획

## 1. 문서 목적

이 문서는 Jev를 이용해 현미경 영상이 허용 가능한 초점 상태인지 판단하고, 장기적으로는
현미경의 Z축을 안전하게 제어하는 autofocus 시스템으로 확장하기 위한 개발 계획을 정의한다.

현재 단계에서는 구현 범위와 실험 방법을 먼저 고정한다. 사용자용 설치 및 사용 설명서는
구현이 안정된 뒤 별도의 영문 `README.md`로 작성한다.

## 2. 작성 언어 원칙

- 이 `PLAN.md`는 프로젝트 내부 계획서이므로 한국어로 작성한다.
- Python 코드, 코드 주석, docstring, 설정 파일, 환경변수, 로그, 오류 메시지와 CLI 문구는
  영어로 작성한다.
- 추후 배포할 `README.md`, API 문서, 예제와 릴리스 노트도 영어로 작성한다.
- 코드 식별자에는 한글을 사용하지 않는다.

## 3. 프로젝트 목표

### 3.1 1차 목표

첫 번째 목적은 Jev가 초점을 잘 찾는지 확인하는 것이다. 연구 질문은 다음 한 문장으로
제한한다.

> 임의의 초기 Z 위치에서 시작한 Jev 기반 controller가 제한된 probe만으로 허용 가능한 초점
> 위치에 도달할 수 있는가?

저장된 Z-stack을 가상 현미경으로 사용해 다음 작업을 수행하는 재현 가능한 offline pipeline을
만든다.

1. 각 현미경 영상에서 일반적인 초점 관련 특징을 계산한다.
2. 특징을 stack 또는 reference 기준으로 정규화한다.
3. 임의의 Z 위치에서 시작해 현재 영상과 최근 probe 이력을 구조화된 Jev state로 변환한다.
4. Jev가 현재 위치를 유지할지, 어느 방향으로 probe할지 또는 더 넓게 탐색할지 판단하게 한다.
5. 선택된 행동에 따라 Z-stack의 다음 이미지를 관측하는 closed-loop simulation을 반복한다.
6. 최종 위치를 알려진 best-focus 및 acceptable-focus 범위와 비교한다.
7. Jev가 단순 focus metric보다 초점을 더 안정적으로 찾는지 평가한다.

첫 MVP에서는 sample 탐지, 장시간 focus 유지, drift 보정과 실제 장비 제어를 평가하지 않는다.

### 3.2 장기 목표

Offline benchmark가 충분한 성능을 보일 경우 다음 closed-loop 흐름으로 확장한다.
카메라 영상 획득과 현미경 장치 제어에는 Micro-Manager의 low-level core를 Python에서 직접
호출하는 `pymmcore.CMMCore`를 사용한다.

```text
Microscope process                 Jev process
  pymmcore capture                   System One API request
  device state                       typed decision response
          |                                  |
          +----------> Controller <----------+
                       feature history
                       state snapshot
                       decision validity gate
                       safety policy
                       command dispatch
```

### 3.3 현재 범위에서 제외하는 작업

- 개별 현미경 제조사 SDK와의 직접 연동
- 실제 Z stage 자동 이동
- Jev 자체의 fine-tuning 또는 LoRA 학습
- 원본 현미경 이미지를 Jev에 직접 입력하는 방식
- 단일 Score 결과로 연속적인 Z 오차를 회귀하는 방식
- 운영 환경 배포 및 장시간 무인 운전

## 4. 핵심 설계 원칙

### 4.1 영상 및 수치 처리는 코드가 담당한다

Jev는 텍스트 또는 JSON state를 받으며 이미지 자체를 입력받지 않는다. 또한 정밀 계산과
수치 비교에 적합한 계산기가 아니므로 다음 작업은 Python에서 결정론적으로 수행한다.

- 이미지 읽기와 채널 변환
- 통계 및 초점 특징 계산
- 정규화와 reference 대비 비율 계산
- Z 위치 정렬과 이동 방향 계산
- 변화량, 기울기와 추세 계산
- 임계값 비교
- 평가 지표와 보정 통계 계산

### 4.2 Jev는 제한된 의미 판단을 담당한다

Jev에는 raw 숫자만 나열하지 않고, 코드가 계산한 값과 의미 있는 구간을 함께 제공한다.
각 질문은 짧고 독립적으로 정의하며 하나의 요청에 가능한 질문을 함께 보낸다.

예상 질문은 다음과 같다.

- `focus_acceptable`: 현재 영상은 정의된 실험 목적에 사용할 수 있는 초점인가?
- `focus_trend`: 최근 이동 이후 초점이 개선, 악화 또는 유지되고 있는가?
- `next_action`: `move_positive_z`, `move_negative_z`, `hold`, `wider_scan` 중 무엇을 해야 하는가?

첫 실험에서는 `focus_acceptable`과 `next_action`을 필수 질문으로 사용한다. `focus_trend`는 방향
판단에 도움이 되는지 비교하기 위한 선택 항목이다. `sample_present` 등 다른 품질 판단은 autofocus
성능을 확인한 이후로 미룬다.

### 4.3 제어권은 항상 결정론적 코드가 가진다

Jev 출력은 stage 명령으로 직접 전달하지 않는다. 다음 조건을 코드에서 확인한 뒤 행동한다.

- 판단 확률과 confidence가 실험으로 정한 threshold 이상인지
- 요청한 행동이 허용된 Z 범위 안인지
- 최대 step 크기와 최대 probe 횟수를 넘지 않는지
- 영상 또는 API 오류가 없는지
- 최근 행동과 판단 사이에 모순이 없는지

불확실하거나 비정상적인 경우 기본 행동은 이동이 아니라 `hold` 또는 `wider_scan`으로 한다.

### 4.4 하드웨어 접근은 `pymmcore` adapter로 격리한다

애플리케이션의 feature, Jev 및 policy 계층은 `pymmcore.CMMCore` 객체를 직접 사용하지 않는다.
카메라 촬영과 Z stage 제어는 공통 microscope interface 뒤의 `PyMMCoreMicroscope` adapter에서만
수행한다. 이 구조를 사용하면 offline Z-stack simulator와 실제 장비가 같은 상위 pipeline을
공유할 수 있다.

`CMMCore` 접근은 한 실행 context가 소유하고 명령을 직렬화한다. 촬영과 stage 이동 사이에는
장치 완료 상태를 확인하며, adapter는 다음 최소 기능을 제공한다.

- Micro-Manager device adapter 검색 경로 설정
- 장비별 Micro-Manager configuration 로딩
- 현재 camera 및 focus device 확인
- single-frame capture와 NumPy array 반환
- 현재 Z 위치 조회
- 제한된 absolute 또는 relative Z 이동
- 장치 대기, 오류 변환과 안전한 종료

실제 장비 이름, serial number, port와 property는 코드에 고정하지 않고 Micro-Manager
configuration 및 로컬 설정에서 읽는다.

### 4.5 `pymmcore`와 Jev는 별도 process에서 실행한다

Runtime은 기본적으로 다음 세 process로 구성한다.

1. **Controller process**: 전체 상태와 정책의 유일한 소유자
2. **Microscope process**: `pymmcore.CMMCore`의 유일한 소유자
3. **Jev process**: TypeSafe API 요청과 응답 처리

가칭 `master.py`의 역할은 단순 message relay가 아니라 controller 또는 orchestrator에 가깝다.
최종 파일명은 역할이 명확한 `controller.py`를 우선 사용한다. Controller는 다음을 담당한다.

- microscope observation 수신 및 시간순 정렬
- 최근 observation의 bounded history 유지
- Jev 요청용 immutable state snapshot 생성
- Jev 응답의 최소 유효성, confidence와 safety 조건 검증
- 유효한 command만 microscope process에 전달
- command acknowledgment, timeout, fault와 process heartbeat 관리
- 모든 observation, decision, rejection과 command 결과 기록

Microscope process는 `CMMCore`를 process 내부에서 생성한다. 부모 process에서 만든 core를
`fork`로 상속하지 않는다. 운영체제에 관계없이 Python multiprocessing의 `spawn` start method를
명시적으로 사용한다. `CMMCore`에는 microscope process만 접근하며 다른 process가 직접 장치를
호출하지 않는다.

Jev process는 하드웨어를 전혀 알지 못한다. Controller가 만든 snapshot을 받아 API를 호출하고,
typed decision을 반환한다. Jev 요청이 지연되거나 실패해도 microscope process의 heartbeat와
안전한 장비 종료는 계속 동작해야 한다.

### 4.6 Jev에는 짧은 sliding window만 전달한다

Controller는 최근 수 초의 observation을 in-memory ring buffer에 보관한다. 정확한 window 길이는
configuration으로 관리하며 초기값은 실험을 통해 결정한다. Jev state에는 다음만 포함한다.

- 요청 시점의 `current_observation`
- 현재 관측 이전의 `history_window_seconds` 이내 observation
- 해당 window 안에서 발생한 Z command와 command result
- calibration reference 및 질문에 직접 필요한 실험 metadata

원본 frame 전체를 Jev에 전달하지 않는다. history에는 정규화된 feature, semantic band, Z 위치,
stage 상태와 monotonic timestamp를 넣는다. frame rate가 높으면 모든 sample을 넣지 않고 고정된
최대 개수로 downsample하거나 feature trend로 요약한다. `current_observation`은 history와 분리해
중복되지 않게 한다.

Window에는 wall-clock 시간이 아니라 monotonic 시간을 사용한다. 로그에는 별도로 UTC timestamp를
기록한다. 이렇게 하면 시스템 시간 변경이 window와 timeout 계산에 영향을 주지 않는다.

### 4.7 느린 시스템을 전제로 최소한의 결정 유효성만 검사한다

현재 대상 시스템에서는 초점 상태의 자연 변화가 Jev 요청 시간보다 충분히 느리다고 가정한다.
따라서 Jev가 판단하는 동안 새 frame이 촬영되더라도 그것만으로 응답을 무효화하지 않는다.
Jev는 요청 시점의 snapshot을 판단하고, Controller는 하드웨어 제어 상태가 실질적으로 바뀌지
않았다면 해당 결정을 그대로 사용할 수 있다.

각 Jev 요청에는 다음 correlation field를 포함한다.

- `request_id`
- `session_id`
- `snapshot_id`
- `current_observation_id`
- `hardware_state_version`
- `created_at_monotonic`
- 요청 당시 `z_um`

`current_observation_id`는 추적과 재현을 위한 값이며, 더 새로운 observation이 생겼다는 사실만으로
응답을 폐기하는 조건으로 사용하지 않는다. `hardware_state_version`은 새 frame마다 증가하지 않고
Z 이동, focus device 변경 또는 판단에 영향을 주는 acquisition 설정이 변경될 때만 증가한다.

응답에도 같은 식별자를 유지한다. Controller는 아래 조건 중 하나라도 해당할 때만 결과를 기록하고
stage command로 변환하지 않는다.

- 시스템의 예상 변화 시간보다 충분히 긴 hard TTL을 초과함
- 요청 이후 새로운 Z 이동이 완료됨
- `hardware_state_version`이 요청 당시와 다름
- microscope process가 ready 상태가 아님
- 더 새로운 요청이 이미 승인 또는 처리됨
- confidence 또는 action probability가 policy threshold 미만임
- 현재 Z와 요청 당시 Z가 허용 오차 이상 다름

즉 일반적인 camera acquisition은 Jev 판단과 병렬로 계속 진행할 수 있다. 다만 Jev 요청 이후
stage 이동이나 노출, ROI, channel처럼 판단의 의미를 바꾸는 설정 변경이 발생하면 해당 응답은
폐기한다. 실제 Jev latency와 초점 변화 시간을 기록해 이 가정이 맞는지는 benchmark에서 확인한다.

Jev 요청은 기본적으로 autofocus session당 하나만 in-flight로 유지한다. 이미 전송된 HTTP 요청을
완전히 취소할 수 없을 때에는 해당 request를 `superseded`로 표시하고 늦게 도착한 응답을 폐기한다.

### 4.8 IPC는 bounded message contract를 사용한다

첫 구현은 local `multiprocessing.Queue`와 typed dataclass 또는 Pydantic message를 사용한다.
Queue는 무제한으로 쌓이지 않게 크기를 제한한다.

- Observation queue: 오래된 frame을 모두 처리하기보다 최신 상태를 우선하는 coalescing 적용
- Jev request queue: snapshot마다 최대 하나, superseded request 식별 가능
- Jev result queue: request correlation field 필수
- Hardware command queue: command ID와 precondition 포함
- Hardware event queue: acknowledgment, completion, device fault와 heartbeat 포함

Stage command는 멱등성을 보장할 수 없으므로 timeout만으로 같은 command를 자동 재전송하지 않는다.
Controller가 command status를 조회하고 명시적으로 복구한다. Controller와 microscope process 양쪽에서
Z 범위와 최대 step을 검사해 방어 계층을 이중화한다.

### 4.9 Process 장애 시 기본 상태는 정지이다

- Controller heartbeat가 끊기면 microscope process는 새로운 이동을 거부한다.
- Jev process가 실패하거나 timeout이면 현재 위치를 유지하고 오류를 보고한다.
- Microscope process가 실패하면 Controller는 모든 pending decision을 무효화한다.
- 종료 시 Controller가 Jev worker를 정리하고 microscope worker에 stop 요청을 보낸다.
- Microscope worker는 acquisition 중단, device wait 및 안전한 core 종료를 수행한다.

Hardware worker가 실행하는 명령에도 자체 유효기간과 precondition을 넣는다. 따라서 Controller가
멈춘 직후 queue에 남아 있던 오래된 이동 명령이 뒤늦게 실행되지 않는다.

## 5. 필요한 데이터

### 5.1 최소 입력 단위

하나의 Z-stack은 다음 정보를 포함해야 한다.

- 동일한 시야를 여러 Z 위치에서 촬영한 이미지 파일
- 각 이미지에 대응하는 `z_um`
- stack 또는 acquisition을 구분하는 고유 식별자
- 사람이 정하거나 검증된 `best_focus_z_um`
- 초점으로 인정할 허용 오차 또는 이미지별 focus label

### 5.2 권장 메타데이터

- imaging mode: brightfield, phase contrast, fluorescence 등
- sample type
- objective magnification과 numerical aperture
- pixel size
- exposure time과 camera gain
- channel 또는 wavelength
- acquisition timestamp
- 시야 또는 실험 식별자

### 5.3 데이터 분할

같은 Z-stack의 인접 이미지가 학습 및 평가 양쪽에 섞이지 않도록 stack 또는 시야 단위로
분할한다. Threshold 조정용 calibration set과 최종 test set도 분리한다.

## 6. Ground truth 정의

두 종류의 정답을 구분한다.

1. `best_focus_z_um`: 해당 stack에서 가장 좋은 초점의 Z 위치
2. `focus_acceptable`: 실험에 사용 가능한 품질인지 나타내는 boolean label

가능하면 다음 필드를 포함하는 manifest를 사용한다.

```text
stack_id,image_path,z_um,best_focus_z_um,focus_acceptable
```

`focus_acceptable`을 `abs(z_um - best_focus_z_um) <= tolerance_um`으로 자동 생성할지,
사람이 영상 품질을 직접 판정할지는 실제 시료와 실험 목적을 확인한 뒤 결정한다.

## 7. 초기 이미지 특징

첫 구현에서는 설명 가능하고 계산 비용이 작은 특징부터 사용한다.

- mean intensity
- intensity standard deviation
- percentile range
- saturated and dark pixel ratio
- Laplacian variance
- Tenengrad 또는 gradient energy
- Fourier high-frequency power ratio
- entropy
- local contrast
- 가능한 경우 object count와 object size statistics

각 특징은 원본 값과 함께 다음 파생값을 만들 수 있다.

- stack 내 robust percentile 또는 median/MAD 기반 정규화 값
- reference 범위 대비 `low`, `typical`, `high` 구간
- 이전 frame 대비 증가, 감소 또는 변화 없음
- 최근 여러 frame의 monotonic trend

특징 목록은 benchmark 결과를 확인한 뒤 추가하거나 제거한다. 처음부터 특정 focus metric 하나를
정답으로 사용하지 않으며, 각 특징의 단독 baseline 성능도 함께 측정한다.

## 8. Jev state 초안

초기 state는 대략 다음 구조를 사용한다. 실제 키와 schema는 구현 과정에서 typed model로
고정한다.

```json
{
  "experiment": {
    "imaging_mode": "fluorescence",
    "sample_type": "example",
    "objective": "100x oil",
    "acceptable_focus_definition": "Defined by the provided ground truth"
  },
  "current": {
    "z_um": 0.2,
    "feature_values": {},
    "feature_bands": {
      "laplacian_variance": "typical",
      "high_frequency_power": "slightly_low"
    }
  },
  "recent_history": [],
  "reference": {
    "acceptable_feature_ranges": {}
  }
}
```

State에는 판단과 관계없는 전체 history를 계속 누적하지 않는다. 최근 몇 개의 관측값과 필요한
reference만 유지해 불필요한 context가 판단을 흐리지 않게 한다.

## 9. 제안하는 코드 구조

```text
src/autofocus_jev/
    __init__.py
    config.py
    controller.py
    history.py
    io.py
    schemas.py
    features.py
    normalization.py
    state_builder.py
    jev_client.py
    policy.py
    evaluation.py
    cli.py
    ipc/
        __init__.py
        messages.py
        queues.py
    workers/
        __init__.py
        microscope.py
        jev.py
    hardware/
        __init__.py
        base.py
        pymmcore_adapter.py
        simulated.py
tests/
    fixtures/
    test_features.py
    test_normalization.py
    test_state_builder.py
    test_policy.py
    test_evaluation.py
    test_hardware_contract.py
    test_history.py
    test_controller.py
    test_ipc.py
```

모듈별 책임은 다음과 같다.

- `io.py`: 이미지와 manifest 로딩, 입력 검증
- `controller.py`: runtime state machine, safety gate와 command dispatch
- `history.py`: time-bounded observation ring buffer와 Jev snapshot 생성
- `schemas.py`: 입력, 특징, state와 결과의 typed schema
- `features.py`: 결정론적 이미지 특징 계산
- `normalization.py`: reference 및 stack 기반 정규화
- `state_builder.py`: Jev에 전달할 최소 JSON state 생성
- `jev_client.py`: TypeSafe SDK 호출과 응답 변환
- `policy.py`: confidence gate, 이동 제한과 fallback
- `evaluation.py`: ground truth 비교와 지표 계산
- `cli.py`: offline 추출, Jev 평가와 benchmark 명령
- `ipc/messages.py`: process 간 observation, request, decision, command와 event schema
- `ipc/queues.py`: bounded queue와 최신 observation 우선 처리
- `workers/microscope.py`: `CMMCore`를 소유하는 hardware worker entry point
- `workers/jev.py`: TypeSafe API를 소유하는 Jev worker entry point
- `hardware/base.py`: 촬영 및 Z 제어를 위한 공통 interface
- `hardware/pymmcore_adapter.py`: `pymmcore.CMMCore` 기반 실제 장비 adapter
- `hardware/simulated.py`: Z-stack 또는 Micro-Manager demo device 기반 simulator

## 10. 구현 단계

### 단계 0: 프로젝트 재현 환경 — 완료

- Git 저장소를 `main` 브랜치로 초기화했다.
- Python 3.12를 `.python-version`으로 지정했다.
- `pyproject.toml`에 runtime 및 development dependency를 정의했다.
- `uv.lock`으로 의존성 버전을 고정했다.
- `.gitignore`에 가상환경, API key, 현미경 데이터와 생성 결과를 제외했다.
- `.env.example`에 TypeSafe 관련 환경변수 예시를 추가했다.
- 최소 패키지와 smoke test를 만들었다.
- `uv sync --frozen`, Pytest, Ruff lint와 format 검사를 통과했다.

현재 주요 의존성은 다음과 같다.

- `typesafe-sdk`
- `pymmcore`
- `numpy`
- `scipy`
- `scikit-image`
- `tifffile`
- 개발용 `pytest`, `pytest-cov`, `ruff`

### 단계 1: 데이터 계약과 loader

- 입력 manifest schema 확정
- TIFF 및 일반 이미지 로더 구현
- 누락된 파일, 잘못된 Z 값과 중복 row 검증
- 작은 synthetic fixture와 단위 테스트 추가

완료 조건은 유효한 stack을 typed record로 읽고, 잘못된 입력에 명확한 영문 오류를 반환하는
것이다.

### 단계 2: 특징 추출 baseline

- 초기 이미지 특징 구현
- grayscale 및 다중 채널 처리 정책 정의
- 상수 영상, NaN, saturation과 작은 영상 edge case 테스트
- 특징을 CSV 또는 JSONL로 저장하는 CLI 추가

완료 조건은 같은 입력에서 같은 특징이 재현되고, synthetic blur sequence에서 주요 sharpness
특징이 예상 방향으로 변하는 것이다.

### 단계 3: 정규화와 state 생성

- calibration data에서 reference statistics 계산
- 숫자를 semantic band로 변환
- 최근 Z 및 특징 변화 추세 생성
- compact Jev state schema와 snapshot test 작성

완료 조건은 ground truth label을 state에 누출하지 않으면서 필요한 context만 포함하는 것이다.

### 단계 4: Jev 연동

- `typesafe-sdk` wrapper 구현
- atomic Noul 및 Choice 질문 정의
- model ID, timeout과 retry 설정
- API key 누락, rate limit, timeout과 malformed response 처리
- 원본 요청과 응답에서 secret을 제외한 experiment log 생성

Threshold calibration 기간에는 moving alias 대신 versioned model ID를 사용한다.

### 단계 5: 단일 관측 판단 benchmark

- Jev 판단을 모든 test sample에 대해 실행
- deterministic baseline과 결과 비교
- focus acceptable 분류 및 next-action 방향 정확도 측정
- probability calibration 분석
- `hold`, `probe`, `wider_scan` threshold 결정

### 단계 6: Closed-loop focus-finding simulator

실제 stage 대신 Z-stack을 가상 현미경으로 사용한다. Controller가 Z 행동을 선택하면 해당 위치의
이미지가 다음 관측으로 반환된다.

- 각 stack에서 여러 무작위 initial Z로 반복 시작
- 현재 관측과 최근 수 초에 해당하는 probe history만 Jev에 전달
- `move_positive_z`, `move_negative_z`, `hold`, `wider_scan` 실행
- 최대 step, 탐색 범위와 probe 횟수 적용
- acceptable focus 도달 여부, final Z error와 probe 수 기록
- 같은 시작점에서 deterministic baseline controller와 비교
- 낮은 confidence 및 API 실패에 대한 fallback 검증

완료 조건은 서로 분리된 calibration/test stack에서 Jev controller가 초점 획득 성공률과 최종 Z
오차를 재현 가능하게 산출하는 것이다.

### 단계 7: Jev 사용 가치에 대한 go/no-go 판단

Closed-loop 결과를 기준으로 Jev가 autofocus에 유효한지 먼저 판단한다.

- focus metric 단독 방식보다 성공률이 높거나 조건 변화에 더 robust한가?
- 추가 probe 수와 API latency가 허용 가능한가?
- confidence가 실제 실패 가능성을 구분하는가?
- 시료, 시야 또는 촬영 조건이 바뀌어도 성능이 유지되는가?

Jev가 baseline보다 의미 있는 가치를 보이지 않으면 process runtime과 실제 장비 제어 구현을
진행하기 전에 state, criteria와 feature representation을 재검토한다.

### 단계 8: Concurrent runtime skeleton

- `spawn` 기반 Controller, microscope worker와 Jev worker 구성
- typed IPC message 및 bounded queue 구현
- monotonic sliding window와 immutable snapshot 구현
- request correlation과 최소 decision validity gate 구현
- heartbeat, worker crash와 graceful shutdown 처리
- fake hardware 및 fake Jev worker를 이용한 deterministic integration test 작성

완료 조건은 새 camera frame이 도착한 경우에는 유효한 Jev 결정을 사용할 수 있지만, 요청 이후
Z 또는 acquisition 설정이 변경된 판단은 stage command로 전달되지 않는 것이다. 어느 worker가
실패해도 hardware state가 안전하게 유지되어야 한다.

### 단계 9: 실제 현미경 adapter

Offline 및 simulator 결과가 목표를 충족한 뒤 `pymmcore.CMMCore` adapter를 활성화한다.
Adapter는 capture, current Z 조회, bounded relative move, device wait와 safe shutdown interface를
구현한다.

`pymmcore` Python wheel은 `uv.lock`으로 재현하지만 실제 장비 구동에는 별도의 Micro-Manager
device adapter 설치가 필요하다. 새 컴퓨터에서는 다음 항목을 확인해야 한다.

- 사용할 camera 및 Z stage를 지원하는 Micro-Manager device adapter 설치
- `pymmcore`와 device adapter의 Device Interface Version 일치
- 해당 컴퓨터에서 Micro-Manager configuration이 정상적으로 로딩되는지 확인
- `MM_DEVICE_ADAPTER_PATH`와 `MM_CONFIG_PATH`를 로컬 환경에 설정
- 제조사 driver 및 vendor library가 운영체제에서 인식되는지 확인

Device Interface Version은 시작 시 `getAPIVersionInfo()`로 기록하고, configuration 로딩 실패는
stage 명령을 허용하지 않는 hard failure로 처리한다. 경로와 configuration 파일은 장비별
자산이므로 기본적으로 Git에 커밋하지 않는다.

## 11. 평가 지표

첫 MVP의 primary endpoint는 `focus acquisition success rate`이다. 각 trial은 무작위 initial Z에서
시작하며, 정해진 최대 probe 횟수 안에 `focus_acceptable == true`인 위치에서 `hold`하면 성공으로
간주한다. Primary endpoint를 해석하기 위해 final absolute Z error와 probe 수를 함께 보고한다.

### 초점 판정

- accuracy
- precision, recall, F1
- ROC-AUC 또는 PR-AUC
- Brier score
- reliability/calibration curve

### 이동 방향 및 제어

- wrong-direction rate
- `hold` precision
- wider-scan 요청 비율
- focus acquisition success rate
- final absolute Z error
- focus 획득까지의 probe 수와 총 이동 거리

### 비교 baseline

- Laplacian variance 최대 위치
- Tenengrad 최대 위치
- Fourier high-frequency power 최대 위치
- 여러 특징의 단순 deterministic composite
- Jev 없이 동일한 threshold policy를 적용한 결과

Jev가 baseline보다 복잡하기 때문에 정확도뿐 아니라 robustness, 불확실성 처리와 시료 조건 간
일반화에서 추가 가치가 있는지도 평가한다.

## 12. 테스트 전략

- 순수 계산 모듈은 API 없이 실행되는 단위 테스트를 작성한다.
- synthetic blur 및 noise 이미지를 이용해 특징의 기본 성질을 검증한다.
- Jev client는 mock response로 대부분 테스트한다.
- hardware contract는 simulator로 테스트하고 실제 장비 테스트는 별도 marker로 분리한다.
- 가능한 경우 Micro-Manager demo camera와 demo stage로 capture/move integration test를 수행한다.
- process test에서는 delayed, duplicated, out-of-order 및 dropped message를 주입한다.
- Z 또는 acquisition 설정 변경 후 도착한 Jev response와 expired hardware command가 실행되지
  않는지 검증한다.
- worker 비정상 종료와 Controller heartbeat 손실 시 fail-safe 동작을 검증한다.
- 실제 API를 호출하는 테스트에는 별도 marker를 붙이고 기본 test run에서는 제외한다.
- 실제 데이터는 Git에 포함하지 않고 작은 공개 가능 fixture만 저장소에 넣는다.
- 매 단계에서 `uv sync --frozen`, `pytest`, `ruff check`, `ruff format --check`를 통과시킨다.

## 13. 재현성과 Git 정책

- 새 환경은 `git clone` 후 `uv sync --frozen`으로 설치한다.
- dependency 변경은 `uv add` 또는 `uv remove`로 수행한다.
- `pyproject.toml`과 `uv.lock`은 항상 함께 커밋한다.
- 실제 `.env`, API key, 원본 데이터와 대용량 결과는 커밋하지 않는다.
- commit message와 branch name은 영어로 작성한다.
- 생성 결과가 논문 또는 보고서에 사용되는 경우 model ID, feature configuration, code commit과
  dataset version을 함께 기록한다.

## 14. 구현 전 결정해야 할 사항

다음 정보가 확보되어야 단계 1 이후의 구현을 정확하게 진행할 수 있다.

1. 첫 데이터의 파일 형식과 디렉터리 구조
2. 이미지별 Z 위치가 파일명, TIFF metadata 또는 별도 표 중 어디에 있는지
3. imaging mode 및 channel 구성
4. best-focus를 정한 방법
5. acceptable-focus의 물리적 또는 시각적 기준
6. 서로 다른 시료, 시야와 촬영 날짜의 수
7. 사용할 Micro-Manager camera 및 focus device label과 configuration 파일
8. 허용 가능한 최대 Z step, Z 범위와 probe 횟수
9. 설치된 device adapter와 `pymmcore`의 Device Interface Version

## 15. 다음 작업

대표 Z-stack과 Z metadata를 확보한 뒤 단계 1의 manifest schema와 loader부터 구현한다. 이후
단계 6의 closed-loop focus-finding simulator까지를 첫 번째 구현 milestone으로 삼는다.

데이터가 아직 준비되지 않았다면 synthetic stack fixture로 전체 흐름만 검증한다. Jev가 초점을
잘 찾는지에 대한 실제 결론은 실제 현미경 Z-stack을 사용한 simulator 결과가 나올 때까지
보류한다. Concurrent runtime과 `pymmcore` 장비 연결은 go/no-go 평가를 통과한 이후에 진행한다.
