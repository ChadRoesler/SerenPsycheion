# seren-psycheion

The console and face workshop for a [SerenSoma](https://github.com/ChadRoesler/SerenSoma) body: watch it and
feel how it's touched, make its faces in **Face Studio**, and run the faces' **build-ups** (a touch kept up
tips the face over). It needs no robot to make faces: install it, open the viewer's **Faces** tab, draw, share.

```
pip install seren-psycheion
seren-psycheion                          # http://127.0.0.1:7429/viewer; Face Studio is its Faces tab (/viewer#faces)
seren-psycheion face build my_face       # the face builder, no server (init | preview | build)
seren-psycheion --describe               # one line of JSON, for installers
```

Faces live in `faces_dir` (`~/seren-psycheion/faces` by default), one folder per face. Art comes in with
Face Studio's **Upload files…** (PNGs, a PixlPunkt `.pxp`, sounds as Ogg Opus); a face goes out to share as
one `.zip` (**Export**) and comes back in with **Import .zip…**. The face format is SerenSoma's
[docs/custom-faces.md](https://github.com/ChadRoesler/SerenSoma/blob/main/firmware/docs/custom-faces.md).

## Configure

Copy [`seren-psycheion.yaml.sample`](seren-psycheion.yaml.sample) to
`~/seren-psycheion/seren-psycheion.yaml` (or point `--config` or
`$SEREN_PSYCHEION_CONFIG` at it):

```yaml
soma:
  robot:
    url: http://192.0.2.21:8080
    token_file: ~/seren-soma/state/control_token
  voice:
    url: http://127.0.0.1:7480
    token_file: ~/seren-soma/state/token
```

The body's tokens are pointers: `token_file`, `token_env` (an environment
variable's name) or `token_keyring` (`service/username` in the OS keychain).
`token:` inline is the escape hatch. Psycheion makes every call to the body
itself; the page never sees these.

## Exposure

Psycheion binds to loopback. It can change a robot's lights and read what a
hand did, so a wider bind with no `server.bearer_token_env` refuses to start
(Meninges' `enforce_server`), and says how to fix it.

## Routes

| | |
|---|---|
| `GET /viewer` | The console, and Face Studio in its Faces tab. A tab can be linked to: `/viewer#faces`. |
| `GET /api/soma` | The robot's status and the voice server's status and settings, each with why it didn't answer if it didn't. |
| `POST /api/soma/robot/settings` | `dimmer`, `volume` (0 to 100), `brightness` (5 to 100); saved on the robot. |
| `POST /api/soma/voice/settings` | Any part of the voice server's settings (night, work hours, the dead-air timer). |
| `GET /api/soma/touch?after=N` | The touch feed since event N, and the hand that's down. |
| `GET /api/buildups` | The face's build-ups: each one's meter, when it last fired, why the last one that filled didn't. |
| `POST /api/buildups` | `{"enabled": true or false}`, until Psycheion restarts (`buildups.enabled` in the config is where it starts). |
| `POST /api/buildups/<name>/test` | Fire one by hand, whatever the hours. Not with a call open. |
| `GET /studio` | Where Face Studio used to be: sends you on to the Faces tab. Its data is under `/api/studio/` (faces, saves, uploads, `.zip` export and import, sending to the robot). |

Everything under `/api` needs the bearer when one is set. Face Studio's stylesheet, script and images
(`/studio/...`) are public to the bearer check, because a `<link>` or an `<img>` can't send a header; the images
check a session cookie the page gets with the bearer instead (HttpOnly, SameSite=Strict, never the bearer itself).

**Build-ups** are the face's own (`buildups:` in its `face.yaml`); the robot carries them and Psycheion runs them
from the voice server's touch feed. Never in the house's work hours (the voice server's settings), never with a
call open.
