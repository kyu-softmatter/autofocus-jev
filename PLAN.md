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
- 안전 제한과 사용자 승인을 두지 않은 실제 Z stage 이동
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
- continuous sequence acquisition과 최신 NumPy frame 제공
- 진단용 single-frame capture
- 현재 Z 위치 조회
- 제한된 absolute 또는 relative Z 이동
- 장치 대기, 오류 변환과 안전한 종료

실제 장비 이름, serial number, port와 property는 코드에 고정하지 않고 Micro-Manager
configuration 및 로컬 설정에서 읽는다.

첫 실제 장비 프로파일은 `bacteria4`와 같은 rig를 대상으로 한다. 따라서 아래 label과 장치 관계를
초기 지원 대상으로 삼되, 시작할 때 loaded device 및 property readback과 일치하는지 검증한다.

- camera: `Kinetix_red`
- XY stage: `XYStage`
- focus drive: Nikon Ti2 `ZDrive`
- hardware focus support: `PFS`와 `PFSOffset`
- pattern illumination: `LightEngine`, 기본 channel `GREEN`, DMD 경유
- autofocus illumination: `Aura`, channel `GREEN`, widefield
- optical path: `Nosepiece`, `CondenserTurret`, `LappMainBranch1`, `LightPath`, CSUW1 장치와 shutter
- SLM/DMD: Micro-Manager가 `getSLMDevice()`로 반환하는 장치

같은 rig에서 camera buffer 접근과 DMD pattern submission이 같은 USB 경로를 공유한다. 이 프로젝트는
별도의 camera thread와 DMD thread가 각자 core를 호출하지 않고 microscope process의 단일 hardware
event loop에 두 작업을 넣어 직렬화한다. 향후 별도 thread가 꼭 필요해지면 `bacteria4`와 같은 공용
USB mutex를 camera polling과 `setSLMImage`/`displaySLMImage` 전체에 적용한다.

Autofocus 실험 중 DMD, light engine과 optical path를 계속 바꿀 필요는 없지만, 이 장치들을 무시해서는
안 된다. session 시작 시 명시된 imaging profile을 적용하고 readback한 뒤 고정하며, Jev 판단 중 해당
상태가 바뀌면 `hardware_state_version`을 증가시켜 이전 판단을 폐기한다. 종료 시에는 모든 light
channel과 intensity를 0으로 만들고 shutter를 닫은 뒤 core를 reset한다. Filter wheel은 자동으로
움직이지 않는다.

`bacteria4`의 Python adapter에는 `XYStage` 제어만 구현되어 있지만 `camera_red_only.cfg`에는 Nikon Ti2
`ZDrive`, `PFS`와 `PFSOffset`이 선언되어 있다. 이 프로젝트는 `ZDrive`를 기본 focus device로 명시하고
`setFocusDevice` 후 readback한다. 다만 이동 방향, 단위, soft limit과 backlash는 수동 시험으로 확인해야
한다. PFS가 활성화되어 있으면 software Z probe와 충돌할 수 있으므로, PFS 상태를 읽을 수 있고 profile이
요구한 상태인지 검증하기 전에는 Z 이동 command를 거부한다.

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

Microscope process 내부에서도 `CMMCore` 호출은 하나의 event loop thread에서 직렬화한다. Camera
buffer polling, metadata readback와 Z command가 모두 같은 실행 경로를 지나도록 하며, 다른 thread가
core를 직접 호출하지 않는다. 이는 참고 프로젝트의 camera/DMD mutex보다 더 단순한 autofocus 전용
규칙이다.

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

### 4.10 Micro-Manager 설정과 애플리케이션 설정을 분리한다

`bacteria4`의 `pars.json` → typed config → device factory 흐름은 재사용하되, 장비 전용 값과
autofocus 정책을 한 파일에 평면적으로 섞지 않는다. 설정은 다음 세 계층으로 나눈다.

1. **Micro-Manager system configuration**: Micro-Manager가 생성한 `.cfg` 파일
2. **Application configuration**: 저장소에 예제로 배포할 `config/autofocus.example.toml`
3. **Secret 및 host override**: Git에서 제외되는 `.env` 또는 명시적 CLI option

Application configuration은 Python 3.12의 `tomllib`로 읽고 typed dataclass로 검증한다. 예상
section은 다음과 같다.

```toml
[hardware]
device_adapter_path = "C:/Program Files/Micro-Manager-2.0"
system_config_path = "C:/path/to/camera_red_only.cfg"
camera_label = "Kinetix_red"
xy_stage_label = "XYStage"
focus_device_label = "ZDrive"

[focus]
pfs_device_label = "PFS"
pfs_offset_device_label = "PFSOffset"
pfs_policy = "require_off"
# pfs_status_property와 pfs_safe_values를 실제 장비에서 확인하기 전에는 Z 이동을 금지한다.

[camera]
roi_xyxy = [0, 2400, 0, 2400]
exposure_ms = 30.0
acquisition_period_s = 0.1
sequence_interval_ms = 0.05
pixel_size_um_override = 0.65

[illumination]
dia_lamp_on = false
auto_shutter = false
pattern_engine = "LightEngine"
pattern_channel = "GREEN"
pattern_intensity = 500
pattern_on = false
focus_engine = "Aura"
focus_channel = "GREEN"
focus_intensity = 50
focus_on = false

[optical_path]
nosepiece_state = "1"
condenser_turret_state = "2"
lapp_main_branch_state = "1"
light_path_state = "3"
csu_dichroic_state = "0"
csu_port_state = "2"
csu_bright_field_port = "Bright Field"
csu_shutter_state = "Closed"
turret1_shutter_on = true
turret2_shutter_on = true

[dmd]
enabled = false
require_device = true
calibration_path = "C:/path/to/calibration_models.json"

[autofocus]
history_window_s = 3.0
probe_step_um = 0.2
max_probes = 20
min_z_um = -10.0
max_z_um = 10.0

[jev]
model = "jev-1.13.0"
request_timeout_s = 10.0
```

위 값은 동일 rig의 `bacteria4/pars.json`을 기반으로 한 초기 profile이며 검증 없이 안전한 값으로
간주하지 않는다. 특히 Aura intensity scale, DMD 방향, pixel size가 대응하는 objective, PFS 상태와
Z limit은 장비에서 확인해야 한다. 경로와 장비별 보정값은 별도의 로컬 configuration에서 명시한다.
`TYPESAFE_API_KEY`는 configuration 파일에 저장하지 않고 환경변수에서만 읽는다.
`roi_xyxy`는 `[x0, x1, y0, y1]`이며 adapter 경계에서 CMMCore의 `(x, y, width, height)` 형식으로
한 번만 변환한다.

설정 필드는 두 종류로 구분한다.

- **Startup-only**: adapter path, system `.cfg`, camera/focus/XY/SLM label, ROI, exposure, acquisition
  period, illumination engine, optical path와 shared-memory shape. 변경하려면 hardware process를
  재시작한다.
- **Runtime policy**: history window, Jev threshold, probe step, 최대 probe 수. 검증 후 session 경계에서
  다시 읽을 수 있다.

Unknown key는 무시하지 않고 오류로 처리해 오타를 조기에 발견한다. 모든 상대 경로는 현재 작업
directory가 아니라 application configuration 파일의 위치를 기준으로 해석한다. 시작할 때 원본
설정, 환경변수 override, hardware readback을 합친 resolved configuration snapshot을 결과 폴더에
저장한다.

### 4.11 Camera frame과 metadata를 함께 다룬다

참고 프로젝트처럼 `startContinuousSequenceAcquisition`을 시작하고 circular buffer를 주기적으로
비운다. 처리 속도가 acquisition보다 느릴 때 backlog를 모두 따라가지 않고 최신 frame을 우선한다.
각 publish에는 연속적인 `frame_id`를 부여해 건너뛴 frame 수를 사후 계산할 수 있게 한다.

정적 camera 정보는 configuration 로딩과 camera 선택이 끝난 직후 한 번 읽어 `CameraInfo`로
기록한다.

- MMCore version과 Device API version
- camera label과 focus device label
- width, height, ROI와 binning
- bytes per pixel, image bit depth와 component 수
- image buffer size
- exposure
- Micro-Manager pixel size와 그 값의 source
- 허용 목록으로 제한한 camera property readback

Micro-Manager의 `pixel_size_um`이 0 또는 유효하지 않으면 application configuration의 override를
사용하고 `pixel_size_source = "config_override"`로 표시한다. 두 값이 모두 없으면 픽셀 크기가
필요한 계산을 중단하며 임의의 값을 추정하지 않는다.

각 frame에는 다음 `FrameMetadata`를 붙인다.

- `frame_id`
- monotonic acquisition timestamp와 UTC log timestamp
- timestamp source; 초기 구현은 camera timestamp가 아닌 `host_after_buffer_pop`으로 명시
- camera label
- shape와 NumPy dtype
- ROI, exposure와 binning
- autofocus illumination engine, channel, on/off와 intensity readback
- frame 획득 시점의 `z_um`
- `hardware_state_version`
- camera buffer에서 버린 frame 수

정보 수집은 read-only API로 수행하고 camera property를 열거했다는 이유로 값을 변경하지 않는다.
Frame과 metadata는 동일한 `frame_id`로 결합하며, shape/dtype가 시작 시 `CameraInfo`와 다르면
해당 frame을 사용하지 않고 hardware fault로 보고한다.

### 4.12 Raw frame data plane은 shared memory를 사용한다

고해상도 `uint16` frame을 `multiprocessing.Queue`로 직접 pickle하지 않는다. Microscope process가
두 개 이상의 shared-memory slot을 소유하고 inactive slot에 frame을 쓴 뒤 작은 frame descriptor만
queue에 게시한다.

Descriptor는 shared-memory name, slot, frame ID, shape, dtype, timestamp와 metadata를 포함한다.
Controller는 해당 slot을 복사한 뒤 frame ID를 다시 확인해 쓰기 도중의 torn read를 검출한다.
Feature 계산이 느려 frame을 놓친 경우 최신 descriptor로 건너뛰며, autofocus는 모든 camera frame을
처리하는 것을 요구하지 않는다.

Shared-memory lifecycle은 다음 원칙을 따른다.

- owner는 microscope process 하나뿐이다.
- 이름에는 session ID를 포함한다.
- 정상 종료 시 owner가 close와 unlink를 수행한다.
- subscriber는 절대 unlink하지 않는다.
- Python `resource_tracker`의 private API를 monkey-patch하지 않는다.
- crash 후 남은 segment 정리는 명시적인 session cleanup 경로로 처리한다.

첫 구현에서는 Controller가 shared-memory frame을 복사해 feature를 계산한다. 측정 결과 feature
계산이 controller responsiveness를 방해할 때에만 별도의 analysis worker를 추가한다.

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
config/
    autofocus.example.toml
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
        shared_frame.py
    workers/
        __init__.py
        microscope.py
        jev.py
    hardware/
        __init__.py
        base.py
        metadata.py
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
    test_config.py
    test_camera_metadata.py
    test_ipc.py
    test_shared_frame.py
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
- `ipc/shared_frame.py`: raw frame double buffer와 frame descriptor
- `workers/microscope.py`: `CMMCore`를 소유하는 hardware worker entry point
- `workers/jev.py`: TypeSafe API를 소유하는 Jev worker entry point
- `hardware/base.py`: 촬영 및 Z 제어를 위한 공통 interface
- `hardware/metadata.py`: `CameraInfo`와 `FrameMetadata` readback 및 검증
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
- MIT License를 적용했다.
- GitHub Actions에서 lockfile, test, lint와 format을 검사하는 CI를 구성했다.
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

- TOML typed configuration loader와 strict validation 구현
- startup-only/runtime field 분류 및 resolved config snapshot 저장
- `spawn` 기반 Controller, microscope worker와 Jev worker 구성
- typed IPC message 및 bounded queue 구현
- synthetic microscope worker와 shared-memory frame double buffer 구현
- 최신 frame 우선 소비와 dropped-frame count 구현
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

구현 순서는 다음과 같다.

1. adapter path 지정 및 Micro-Manager `.cfg` 로딩
2. `Kinetix_red`, `XYStage`, `ZDrive`, `PFS`, DMD, 두 light engine과 optical-path device 존재 여부 확인
3. illumination은 off인 상태로 camera의 `Exposure`, `ShutterMode = Never`, `Port = Dynamic Range` 적용
4. configuration 순서대로 optical path를 적용하고 각 property의 allowed value 및 readback 검증
5. camera/focus/XY/SLM device를 core에 지정하고 readback하며 PFS precondition이 불명확하면 이동 비활성화
6. `CameraInfo`, 전체 hardware profile과 MMCore/Device API version 수집 및 저장
7. continuous sequence acquisition 시작
8. camera buffer를 제한된 수만큼 drain하고 최신 frame을 shared memory에 publish
9. frame마다 Z 위치, illumination/optical-path version과 `FrameMetadata` 결합
10. camera polling, DMD submission과 모든 core command를 단일 event loop에서 직렬화
11. 별도의 승인된 hardware test에서 bounded absolute/relative focus move와 `waitForDevice` 검증
12. acquisition 중단, 모든 light/intensity off, shutter close, core reset 순서로 idempotent shutdown 구현

`pymmcore` Python wheel은 `uv.lock`으로 재현하지만 실제 장비 구동에는 별도의 Micro-Manager
device adapter 설치가 필요하다. 새 컴퓨터에서는 다음 항목을 확인해야 한다.

- 사용할 camera 및 Z stage를 지원하는 Micro-Manager device adapter 설치
- Kinetix, DMD, `LightEngine`, `Aura`, `XYStage` 제조사 driver 및 vendor library 설치
- `pymmcore`와 device adapter의 Device Interface Version 일치
- 해당 컴퓨터에서 Micro-Manager configuration이 정상적으로 로딩되는지 확인
- `MM_DEVICE_ADAPTER_PATH`와 `MM_CONFIG_PATH`를 로컬 환경에 설정
- 제조사 driver 및 vendor library가 운영체제에서 인식되는지 확인

`bacteria4`의 `camera_red_only.cfg`와 DMD calibration은 동일 하드웨어를 설명하는 중요한 입력이다.
라이선스와 장비 정보 공개 범위를 확인한 뒤 공개 가능한 예제 profile은 Git에 추가하고, serial number,
host 경로 또는 비공개 calibration이 포함된 원본은 로컬 파일로 유지한다.

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
- configuration의 unknown key, 잘못된 경로, label 누락과 Z limit 역전을 테스트한다.
- synthetic blur 및 noise 이미지를 이용해 특징의 기본 성질을 검증한다.
- Jev client는 mock response로 대부분 테스트한다.
- hardware contract는 simulator로 테스트하고 실제 장비 테스트는 별도 marker로 분리한다.
- 가능한 경우 Micro-Manager demo camera와 demo stage로 capture/move integration test를 수행한다.
- process test에서는 delayed, duplicated, out-of-order 및 dropped message를 주입한다.
- shared-memory test에서는 shape/dtype mismatch, overwritten slot, torn-read 검출과 owner cleanup을
  검증한다.
- camera metadata test에서는 readback과 config override의 source가 명확히 기록되는지 검증한다.
- rig hardware test에서는 `Kinetix_red`, `XYStage`, DMD, 두 light engine과 optical-path label을 열거하고
  profile과 다른 항목을 property 변경 전에 실패시킨다.
- camera acquisition과 DMD pattern submission이 겹치는 시험으로 USB 직렬화가 유지되는지 검증한다.
- startup 중간 실패와 정상 종료 모두에서 illumination off, intensity 0과 shutter close를 확인한다.
- PFS 상태를 읽지 못하거나 `pfs_policy`와 다르면 `ZDrive` command가 거부되는지 검증한다.
- Z 또는 acquisition 설정 변경 후 도착한 Jev response와 expired hardware command가 실행되지
  않는지 검증한다.
- worker 비정상 종료와 Controller heartbeat 손실 시 fail-safe 동작을 검증한다.
- 실제 API를 호출하는 테스트에는 별도 marker를 붙이고 기본 test run에서는 제외한다.
- 실제 hardware test는 Micro-Manager install path와 `.cfg` 존재를 확인한 경우에만 수집하고,
  stage를 움직이는 테스트는 별도의 명시적 option 없이는 실행하지 않는다.
- 실제 데이터는 Git에 포함하지 않고 작은 공개 가능 fixture만 저장소에 넣는다.
- 매 단계에서 `uv sync --frozen`, `pytest`, `ruff check`, `ruff format --check`를 통과시킨다.

## 13. 재현성과 Git 정책

- 새 환경은 `git clone` 후 `uv sync --frozen`으로 설치한다.
- 저장소는 MIT License로 공개한다.
- `main` push와 pull request마다 GitHub Actions CI를 실행한다.
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
7. 동일 rig에서 사용할 Micro-Manager configuration 파일과 PFS 운용 정책
8. `ZDrive`의 허용 가능한 최대 Z step, Z 범위, 이동 부호, backlash와 probe 횟수
9. 설치된 device adapter와 `pymmcore`의 Device Interface Version

## 15. 다음 작업

대표 Z-stack과 Z metadata를 확보한 뒤 단계 1의 manifest schema와 loader부터 구현한다. 이후
단계 6의 closed-loop focus-finding simulator까지를 첫 번째 구현 milestone으로 삼는다.

데이터가 아직 준비되지 않았다면 synthetic stack fixture로 전체 흐름만 검증한다. Jev가 초점을
잘 찾는지에 대한 실제 결론은 실제 현미경 Z-stack을 사용한 simulator 결과가 나올 때까지
보류한다. Concurrent runtime과 `pymmcore` 장비 연결은 go/no-go 평가를 통과한 이후에 진행한다.

## 16. `bacteria4` 참고 결과

이 계획은 `bacteria4`의 문서에 적힌 지시를 그대로 따르지 않고 실제 source와 test에서 확인되는
구조를 참고했다.

### 참고한 파일

- `lib/scope/config.py`: dataclass config, startup-only locked field와 snapshot
- `lib/core_mm.py`: adapter path, system configuration 로딩과 idempotent close
- `lib/devices_mm.py`: continuous acquisition, circular buffer drain과 최신 frame 유지
- `lib/ring.py`: single-slot latest-value cache와 sequence number
- `lib/viz.py`: 별도 process에 대한 shared-memory frame 전달
- `lib/scope/runner.py`: cooperative stop과 worker lifecycle
- `lib/scope/devices.py`: real/mock device factory와 partial-open cleanup
- `tests/hardware/`: 실제 rig가 있을 때만 실행되는 hardware test gate

### 채택하는 패턴

- configuration을 typed object로 변환한 뒤 device layer에 전달한다.
- Micro-Manager `.cfg`가 device 선언을 담당하고 application config가 label과 autofocus policy를
  담당한다.
- `CMMCore` instance는 하나의 hardware owner만 사용한다.
- camera는 continuous acquisition을 사용하고 느린 consumer가 backlog를 따라가지 않게 최신 frame을
  우선한다.
- 모든 frame에 sequence ID와 monotonic timestamp를 붙인다.
- raw frame은 shared memory, 작은 control/metadata는 queue로 분리한다.
- real/mock 구현은 공통 interface와 factory 뒤에 둔다.
- hardware가 일부 열린 상태에서 실패해도 close가 반복 호출 가능하도록 만든다.
- hardware test와 stage-motion test를 일반 CI에서 분리한다.

### 동일 rig에서 직접 재사용할 하드웨어 계약

- 기본 camera label은 `Kinetix_red`, XY stage label은 `XYStage`로 둔다.
- 기본 focus label은 `ZDrive`로 두며 `PFS`와 `PFSOffset`을 별도 hardware state로 추적한다.
- Kinetix의 `Exposure`, `ShutterMode = Never`, `Port = Dynamic Range` 적용 순서를 보존하고 readback한다.
- DMD submission은 `setSLMImage` → `displaySLMImage` → `waitForDevice` 순서를 사용한다.
- camera buffer 접근과 DMD submission은 동일 USB 임계 구역으로 취급한다.
- pattern source `LightEngine`과 autofocus source `Aura/GREEN`을 별개 장치로 유지한다.
- `Nosepiece`부터 CSUW1와 shutter까지 optical-path 적용 순서를 profile에 보존한다.
- 종료 시 두 light engine의 channel과 intensity를 모두 0으로 만들고 shutter를 닫는다.
- filter wheel은 자동으로 이동하지 않으며, DMD 180도 flip 여부는 calibration 실험으로 확정한다.

### 그대로 채택하지 않는 부분

- 장비 label과 시작 순서는 같은 rig의 초기 profile로 재사용하지만 serial number, Windows 경로와
  calibration 파일은 소스 코드에 복사하지 않는다.
- `bacteria4`의 XY stage adapter를 Z stage adapter로 간주하지 않고 `ZDrive` adapter를 새로 구현한다.
- DMD와 조명이 있다는 이유만으로 autofocus session 중 pattern 또는 light state를 자동 변경하지 않는다.
- 현재 property 값이 유효하다는 가정 대신 allowed values와 적용 후 readback을 검사한다.
- shared-memory 관리를 위해 Python `resource_tracker` private API를 수정하지 않는다.
- unknown configuration key를 조용히 무시하지 않는다.
- Micro-Manager가 보고하는 pixel size를 무조건 신뢰하지 않고 source와 fallback을 기록한다.

### 참고 코드보다 강화하는 부분

- frame header와 raw buffer의 원자성을 sequence 재확인 또는 double buffer로 보장한다.
- width와 height 외에 bit depth, bytes per pixel, component 수, ROI, binning과 exposure를 수집한다.
- `frame_id`, camera metadata와 Z readback을 하나의 immutable record로 결합한다.
- config path는 config 파일 기준으로 해석하고 resolved config를 매 실행에 보존한다.
- CMMCore를 process뿐 아니라 하나의 worker thread에서만 호출해 동시 접근 범위를 최소화한다.

## 17. 구현 현황

현재 첫 번째 코드 단위로 다음 항목을 구현했다.

- `config/autofocus.example.toml`: 동일 rig의 label과 안전한 기본 off 상태를 담은 영문 예제
- `config.py`: strict TOML loader, typed frozen config, 환경변수 경로 override와 host path 검증
- `features.py`: Laplacian variance, Tenengrad, 고주파 power ratio, gradient entropy와 노출 특징
- `hardware/metadata.py`: immutable `CameraInfo`, `FrameMetadata`와 `CapturedFrame`
- `hardware/pymmcore_adapter.py`: 동일 rig preflight, Kinetix 설정, optical path, 연속 acquisition,
  최신 frame 선택, Z/PFS safety gate와 fail-safe shutdown
- fake CMMCore 기반 단위 테스트: 실제 장비 없이 device/property 검증, frame metadata, PFS 및 Z 제한,
  종료 시 조명과 shutter off 동작을 확인

아직 구현하지 않은 주요 항목은 shared-memory frame transport, 세 process runtime, offline Z-stack
manifest, feature normalization, Jev client/state schema와 closed-loop controller이다. DMD device는 시작 시
존재를 확인하지만 pattern submission은 아직 구현하지 않았으므로 `dmd.enabled = true`를 fail-closed로
거부한다.
