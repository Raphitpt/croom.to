# Croom on macOS

Runs Croom on a Mac (a Mac mini behind the meeting room TV): it joins the
room's Google Meet meetings from its calendar and turns a Fire TV on and
off over the network.

On macOS, meetings run in Google Chrome, which handles the camera, the
microphone and the speakers. Croom's own audio/video services, PTZ camera
control and HDMI-CEC are Linux-only and stay inactive.

## Install

Log in with the account the room will use, then:

```bash
git clone https://github.com/amirhmoradi/croom.to.git
cd croom.to
./packaging/macos/install.sh --power-settings
```

This installs Python 3.12, `adb` and Google Chrome with Homebrew, puts
Croom in `~/Library/Application Support/Croom/venv`, writes
`~/.config/croom/config.yaml` and starts Croom at login with the
LaunchAgent `to.croom.agent` (restarted if it crashes).
`--power-settings` stops the Mac from sleeping and restarts it after a
power cut.

Then:

1. **Sign the room account in to Google once.** Meetings run in a
   persistent browser profile; without a signed-in account the room joins
   as a guest and has to be admitted every time.

   ```bash
   launchctl bootout gui/$(id -u)/to.croom.agent
   ~/Library/Application\ Support/Croom/venv/bin/croom-login
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/to.croom.agent.plist
   ```

2. **Allow the camera and microphone** when macOS asks, the first time a
   meeting starts. An MDM profile cannot grant these in advance: macOS
   only lets a user allow them.

3. **Automatic login**: System Settings > Users & Groups > Automatic
   login. macOS disables it while FileVault is on; if your MDM enforces
   FileVault, someone has to log in after each restart.

## Standby screen

Between meetings the display shows the room name, the time and the next
meetings of the room calendar, with a countdown to the next one. Set
`room.language` (`fr` or `en`) and optionally `room.logo_path` (SVG or
PNG, shown in white). If Chrome is closed or crashes, Croom relaunches it
within 15 seconds.

## Calendar

The room joins the meetings of its Google calendar that have a Meet link,
including a meeting already in progress when the Mac starts, and leaves
at their end time (`meeting.auto_leave`).

Set `calendar.google_credentials_path` to either:

- a **service account key** (Google Workspace), with domain-wide
  delegation for `https://www.googleapis.com/auth/calendar.readonly` and
  `calendar.google_delegate_email` set to the room account; or
- an **OAuth token** of the room account (a `token.json` written by
  google-auth).

## Fire TV

Fire OS TVs (Xiaomi, Toshiba, Insignia...) are controlled with ADB over
the network, because a Mac has no HDMI-CEC over USB-C/HDMI.

1. On the TV: Settings > My Fire TV > About, click the device name
   7 times, then Developer options > **ADB debugging** on.
2. Reserve the TV's IP address in the router, and prefer Ethernet: over
   Wi-Fi the TV may drop off the network in deep standby.
3. Set `display.firetv_host`, then find the input the Mac is plugged into:

   ```bash
   adb connect 192.168.1.50:5555     # accept the prompt on the TV
   adb shell dumpsys tv_input | grep -o '[^ ]*/HW[0-9]*' | sort -u
   ```

   Put the matching id in `display.firetv_hdmi_input`.

Croom wakes the TV and switches to that input before joining a meeting,
and puts it in standby afterwards if `display.power_off_after_meeting` is
on. A Pulse-Eight USB-CEC adapter is the alternative for TVs that stop
answering ADB in standby.

## Operate

| | |
|---|---|
| Logs | `~/Library/Logs/Croom/croom.log` |
| Restart | `launchctl kickstart -k gui/$(id -u)/to.croom.agent` |
| Stop until next login | `launchctl bootout gui/$(id -u)/to.croom.agent` |
| Uninstall | `./packaging/macos/uninstall.sh` (`--purge` also removes config and browser profile) |
