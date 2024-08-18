import os
import sys
import subprocess
import time
import datetime
import json
import x11_api
import random
import ctypes
import log
import com_base
import com_tcp
import com_udp
import backend_base
import backend_winmm
import backend_sdl2
import backend_fmodex


class App:
    def __init__(self, argv: any) -> None:
        self.exit_code = 1
        self.first_arg = argv[0]
        self.argv = argv[1:]
        self.is_le = sys.byteorder == 'little'
        self.cwd = os.path.dirname(__file__) or os.getcwd()
        self.encoding = 'utf-8'
        if sys.platform == 'win32':
            self.sig_kill = 0
            self.paths = [self.cwd] + (os.getenv('PATH') or '').split(';')
            self.auto_postfix = ''
            self.auto_prefix = ''
            self.load_library = ctypes.windll.LoadLibrary
        else:
            self.sig_kill = 9
            self.auto_postfix = '.so'
            self.auto_prefix = 'lib'
            self.load_library = ctypes.CDLL
            self.paths = [self.cwd] + (os.getenv('LD_LIBRARY_PATH') or '').split(':')\
                + (os.getenv('PATH') or '').split(':')
        self.config_path = os.path.join(self.cwd, 'config.json')
        if not os.path.isfile(self.config_path):
            self.write_json(
                os.path.join(self.cwd, 'config.json'), self.read_json(os.path.join(self.cwd, 'default_config.json'))
            )
        self.config = self.read_json(self.config_path)
        log.enable_logging = self.config['allow_logging']
        try:
            if '--client-only' in self.argv or (self.config['need_server_arg'] and '--server-only' not in self.argv):
                raise RuntimeError('Client Only!')
            if self.config['com_type'] == 'tcp':
                self.server: com_base.BaseServer = com_tcp.TCPServer(self)
            elif self.config['com_type'] == 'udp':
                self.server: com_base.BaseServer = com_udp.UDPServer(self)
            else:
                raise FileNotFoundError('Unknown communication type')
        except RuntimeError:
            if '--server-only' in self.argv:
                raise RuntimeError('Server Only!')
            if self.config['com_type'] == 'tcp':
                self.client: com_base.BaseClient = com_tcp.TCPClient(self)
            elif self.config['com_type'] == 'udp':
                self.client: com_base.BaseClient = com_udp.UDPClient(self)
            else:
                raise FileNotFoundError('Unknown communication type')
            if self.argv and not (len(self.argv) <= 1 and self.argv[0] == '--client-only'):
                self.client.send(';'.join(self.argv))
                self.exit_code = 0
                # self.client.send('disconnect')
            else:
                self.client_prompt()
            self.client.destroy()
            # self.exit_code = 0
            return
        x11_api.init()
        self.someblocks_pid = 0
        if self.config['someblocks_support']:
            output = subprocess.check_output(['pidof', 'someblocks'], encoding=self.encoding).split(
                '\n'
            )[0].strip().split(' ')[0]
            if not output.isdigit():
                raise RuntimeError('Failed to find someblocks pid')
            self.someblocks_pid = int(output.strip())
        if self.config['audio_backend'] == 'sdl2':
            self.search_libs('libopusfile-0', 'libopus-0', 'libogg-0', 'libmodplug-1')
            self.bk: backend_base.BaseBackend = backend_sdl2.SDL2Backend(
                self, self.search_libs('SDL2', 'SDL2_mixer', prefix=self.auto_prefix)
            )
        elif self.config['audio_backend'] == 'fmod':
            if sys.platform == 'win32':
                self.search_libs('VCRUNTIME140_APP')
            self.search_libs('libfsbvorbis64')
            self.bk: backend_base.BaseBackend = backend_fmodex.FmodExBackend(
                self, self.search_libs('opus', 'media_foundation', 'fsbank', 'fmod', prefix=self.auto_prefix)
            )
        elif self.config['audio_backend'] == 'winmm' and sys.platform == 'win32':
            self.bk: backend_base.BaseBackend = backend_winmm.WinMMBackend(self, ctypes.windll.winmm)
        else:
            raise FileNotFoundError('Unknown audio backend')
        if self.config['force_try_init']:
            for i in range(20):
                try:
                    self.bk.init()
                    break
                except RuntimeError as _err:
                    if i == 19:
                        raise _err
                    time.sleep(0.25)
                    continue
        else:
            self.bk.init()
        self.display_info()
        self.volume = self.config['volume']
        self.speed = self.config['speed']
        if self.volume > 1.0:
            raise RuntimeError(f'Volume {self.volume} is bigger than 1.0')
        self.full_list = []
        self.temp_list = []
        self.full_list_group = {}
        self.rescan()
        self.current_music: base_backend.BaseMusic = None # noqa
        self.running = True
        self.default_track_id = -1
        self.next_is_switch_to_main = False
        try:
            self.main_loop()
            self.should_kill = self.server.should_kill
        except KeyboardInterrupt:
            self.should_kill = self.server.should_kill
        self.cleanup()
        self.bk.quit()
        self.bk.destroy()
        x11_api.destroy()
        self.exit_code = 0
        if self.should_kill:
            os.kill(os.getpid(), self.sig_kill)  # FIXME
    
    def rescan(self) -> None:
        self.full_list.clear()
        self.full_list_group.clear()
        for arg in self.argv:
            ext = arg.split('.')[-1].lower()
            if ext not in self.config['formats']:
                continue
            self.full_list.append(arg)
        if not self.full_list and self.config['music_path']:
            for fn in os.listdir(self.config['music_path']):
                ext = fn.split('.')[-1].lower()
                if ext not in self.config['formats']:
                    continue
                self.full_list.append(os.path.join(self.config['music_path'], fn))
        for track_fp in self.full_list:
            music_group = os.path.basename(track_fp).split(' - ')[0].strip()
            if music_group in self.full_list_group:
                self.full_list_group[music_group].append(track_fp)
            else:
                self.full_list_group[music_group] = [track_fp]
        if self.config['main_playlist_mode'] == 'random_pick':
            random.shuffle(self.full_list)
        log.info('Music scan results:', len(self.full_list), 'tracks in the full list')

    def track_loop(self) -> None:
        if (self.config['print_json'] and self.config['print_json_time']) or 1:
            last_date = ''
            len_format = self.format_time(self.current_music.length)
        while self.running and self.current_music and self.current_music.is_playing():
            self.server.update()
            self.poll_commands()
            self.bk.update()
            if (self.config['print_json'] and self.config['print_json_time']) or 1:
                now_date = datetime.datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                if not now_date == last_date:  # noqa
                    cur_format = self.format_time(self.current_music.get_pos())
                    '''output = {
                        'text': '[' + cur_format + '/' + len_format + '] ' +  # noqa
                                self.current_music.fn_no_ext,
                        'class': 'custom-mediaplayer',
                        'alt': 'mediaplayer'
                    }
                    sys.stdout.write(json.dumps(output) + '\n')
                    sys.stdout.flush()'''
                    text = self.current_music.fn_no_ext + ' [' + cur_format + '/' + len_format
                    text += '] | '
                    text += now_date
                    last_date = now_date
                    x11_api.set_status(text)

    def next_track(self) -> any:
        # TODO: maybe allow to change mode in real time?
        if self.temp_list:
            fp = self.temp_list.pop(0)  # Only default and random pick modes currently
            if not self.temp_list:
                self.next_is_switch_to_main = True
            try:
                return self.bk.open_music(fp)
            except RuntimeError:
                return None
        if self.next_is_switch_to_main:
            self.next_is_switch_to_main = False
            log.info('Switched back to main list')
        if self.config['main_playlist_mode'] in ('default', 'random_pick'):
            self.default_track_id += 1
            if self.default_track_id >= len(self.full_list):
                if self.config['main_playlist_mode'] == 'random_pick':
                    random.shuffle(self.full_list)
                self.default_track_id = 0
            fp = self.full_list[self.default_track_id]
            try:
                return self.bk.open_music(fp)
            except RuntimeError:
                return None
        elif self.config['main_playlist_mode'] == 'random_full':
            fp = random.choice(self.full_list)
            try:
                return self.bk.open_music(fp)
            except RuntimeError:
                return None
        elif self.config['main_playlist_mode'] == 'random_group':
            group_tracks = random.choice(tuple(self.full_list_group.values()))
            fp = random.choice(group_tracks)
            try:
                return self.bk.open_music(fp)
            except RuntimeError:
                return None
        return None

    @staticmethod
    def format_time(need_time: float) -> str:
        round_time = round(need_time)
        sec_str = str(round(round_time) % 60)
        return str(int(round_time / 60)) + ':' + ('0' if len(sec_str) <= 1 else '') + sec_str

    def main_loop(self) -> None:
        pause_first = self.config['pause_first']
        while self.running:
            mus: backend_base.BaseMusic = self.next_track()
            while not mus:
                mus = self.next_track()
            self.play_new_music(mus)
            if pause_first:
                mus.set_paused(True)
                pause_first = False
            stat = os.stat(mus.fp)
            info = mus.fn_no_ext
            if mus.length:
                info += f' [{self.format_time(mus.length)}]'
            if mus.freq:
                info += f' [{int(mus.freq)}Hz]'
            if not mus.type == 'none':
                info += f' [{mus.type.upper()}]'
            if stat.st_mtime:
                info += f' [{str(datetime.datetime.fromtimestamp(int(stat.st_mtime)))}]'
            log.info(info)
            if self.config['print_json'] and not self.config['print_json_time']:
                output = {
                    'text': mus.fn_no_ext,
                    'class': 'custom-mediaplayer',
                    'alt': 'mediaplayer'
                }
                sys.stdout.write(json.dumps(output) + '\n')
                sys.stdout.flush()
            if self.config['current_music_info_path']:
                f = open(self.config['current_music_info_path'], 'w', encoding=self.encoding)
                f.write(mus.fn_no_ext)
                f.close()
            if self.someblocks_pid:
                os.kill(self.someblocks_pid, 34 + 10)
            self.track_loop()

    def play_new_music(self, mus: backend_base.BaseMusic) -> None:
        if self.current_music:
            self.current_music.stop()
            self.current_music.destroy()
        mus.play()
        mus.set_volume(self.volume)
        mus.set_speed(self.speed)
        self.current_music = mus

    def poll_commands(self) -> None:
        temp_mus = []
        while self.server.commands:
            cmds = self.server.commands.pop(0)
            for _cmd in cmds.split(';'):
                cmd = _cmd.strip()
                if os.path.isfile(cmd) and cmd.split('.')[-1].lower() in self.config['formats']:
                    temp_mus.append(cmd)
                    continue
                if cmd == 'next':
                    if self.current_music:
                        self.current_music.stop()
                elif cmd in ('toggle_pause', 'pause', 'resume'):
                    if self.current_music:
                        if cmd == 'toggle_pause':
                            paused = not self.current_music.paused
                        else:
                            paused = cmd == 'pause'
                        self.current_music.set_paused(paused)
                        log.info('Paused:', self.current_music.paused)
                elif cmd == '--client-only' or cmd == '--server-only':
                    pass
                elif cmd == 'clear_temp':
                    self.temp_list.clear()
                    if self.current_music:
                        self.current_music.stop()
                    # log.info('Temp music list cleared')
                elif cmd == 'show_pos':
                    if self.current_music:
                        log.info(f'Music Position: {self.format_time(self.current_music.get_pos())}')
                elif cmd == 'rewind':
                    if self.current_music:
                        self.current_music.rewind()
                elif cmd.startswith('pos_sec'):
                    if not self.current_music:
                        continue
                    try:
                        new_pos = float(cmd.split(' ')[-1])
                    except (ValueError, IndexError) as _err:
                        log.warn(f'Could not convert position value:', _err)
                        continue
                    if cmd.startswith('pos_sec '):
                        cur_pos = 0.0
                    else:
                        cur_pos = self.current_music.get_pos()
                    need_pos = max(min(cur_pos + new_pos, 60.0 * 60.0 * 100.0), 0.0)
                    self.current_music.set_pos(need_pos)
                    got_pos = self.current_music.get_pos()
                    log.info('New Position:', self.format_time(got_pos))
                elif cmd.startswith('pos_rel'):
                    if not self.current_music or not self.current_music.length:
                        continue
                    try:
                        new_pos = float(cmd.split(' ')[-1])
                    except (ValueError, IndexError) as _err:
                        log.warn(f'Could not convert position value:', _err)
                        continue
                    if cmd.startswith('pos_rel '):
                        cur_pos = 0.0
                    else:
                        cur_pos = self.current_music.get_pos() / self.current_music.length
                    need_pos = max(min(cur_pos + new_pos, 100.0), 0.0)
                    self.current_music.set_pos(need_pos * self.current_music.length)
                    got_pos = self.current_music.get_pos()
                    log.info('New Position:', self.format_time(got_pos))
                elif cmd.startswith('volume'):
                    try:
                        new_volume = float(cmd.split(' ')[-1])
                    except (ValueError, IndexError) as _err:
                        log.warn(f'Could not convert volume value:', _err)
                        continue
                    if cmd.startswith('volume '):
                        self.volume = 0.0
                    self.volume = max(min(self.volume + new_volume, 1.0), 0.0)
                    if self.current_music:
                        self.current_music.set_volume(self.volume)
                    log.info('New Volume:', self.volume)
                elif cmd.startswith('speed'):
                    try:
                        new_speed = float(cmd.split(' ')[-1])
                    except (ValueError, IndexError) as _err:
                        log.warn(f'Could not convert speed value:', _err)
                        continue
                    if cmd.startswith('speed '):
                        self.speed = 0.0
                    self.speed = max(min(self.speed + new_speed, 1000.0), -1000.0)
                    if self.current_music:
                        self.current_music.set_speed(self.speed)
                    log.info('New Speed:', self.speed)
                elif cmd == 'rescan':
                    self.rescan()
                elif cmd == 'exit' or cmd == 'quit':
                    self.running = False
                else:
                    log.warn('Unknown Command', cmd)
        if temp_mus:
            self.temp_list = temp_mus
            self.temp_list_prepare()
            log.info('Playing Temp Playlist')
            if self.current_music:
                self.current_music.stop()

    def temp_list_prepare(self) -> None:
        if self.config['main_playlist_mode'] == 'default' and self.temp_list[-1] in self.full_list:
            self.default_track_id = self.full_list.index(self.temp_list[-1]) + 1
        if self.config['temp_playlist_mode'] == 'random_pick':  # Trick
            random.shuffle(self.temp_list)

    def display_info(self) -> None:
        log.info('Welcome to the Pixelsuft MUST!')
        log.info('Found Drivers:', ', '.join(self.bk.get_audio_drivers()))
        log.info('Current Driver:', self.bk.get_current_audio_driver())
        log.info('Found Devices:', ', '.join(self.bk.get_audio_devices_names()))
        log.info('Current Device:', self.bk.get_current_audio_device_name())

    def cleanup(self) -> None:
        if self.server:
            self.server.destroy()
            # self.server = None
        if self.current_music:
            self.current_music.stop()
            self.current_music.destroy()
            self.current_music = None

    def client_prompt(self) -> None:
        msg = 'i_want_to_live_please_do\'nt_die'
        while msg is not None:
            try:
                self.client.send(msg)
                if msg == 'disconnect' or msg == 'exit' or msg == 'quit':
                    self.exit_code = 0
                    return
            except RuntimeError:
                return
            msg = input('>>> ')

    def read_json(self, fp: str) -> dict:
        f = open(fp, 'r', encoding=self.encoding)
        content = f.read()
        f.close()
        return json.loads(content)

    def write_json(self, fp: str, content: dict) -> int:
        f = open(fp, 'w', encoding=self.encoding)
        result = f.write(json.dumps(content, indent=4))
        f.close()
        return result

    def stb(self, str_to_encode: str, encoding=None) -> bytes:
        return str_to_encode.encode(encoding or self.encoding, errors='replace')

    def bts(self, bytes_to_decode: bytes, encoding=None) -> str:
        return bytes_to_decode.decode(encoding or self.encoding, errors='replace')

    def search_libs(self, *libs: any, prefix: str = '') -> dict:
        result = {}
        for path in self.paths:
            for lib in libs:
                if result.get(lib):
                    continue
                try:
                    result[lib] = self.load_library(
                        os.path.join(path, prefix + lib + self.auto_postfix)
                    )
                except (FileNotFoundError, OSError):
                    continue
        for lib in libs:
            if result.get(lib):
                continue
            try:
                result[lib] = self.load_library(prefix + lib + self.auto_postfix)
            except (FileNotFoundError, OSError):
                continue
        return result


if __name__ == '__main__':
    sys.exit(App(sys.argv).exit_code)
