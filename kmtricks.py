#!/usr/bin/env python3
import sys
import os
import abc
import time
import argparse
import subprocess
import io
import heapq
import logging
from collections import OrderedDict as odict
from collections import defaultdict as ddict
from typing import List, Dict, Union, Optional, TextIO, Set, Tuple
from signal import SIGABRT, SIGFPE, SIGILL, SIGINT, SIGSEGV, SIGTERM, Signals
from types import FunctionType
from shutil import rmtree
from copy import deepcopy, copy

__version__ = '0.0.1'

MIN_PYTHON = (3, 5)
if sys.version_info < MIN_PYTHON:
    sys.exit('Python {}.{} or later is required\n'.format(*MIN_PYTHON))

mode_kh = {
    'ascii' : 0,
    'bin'   : 0,
    'pa'    : 0,
    'bf'    : 1,
    'bf_trp': 1
}

control = {
    'all':    6,
    'repart': 1,
    'superk': 2,
    'count':  3,
    'merge':  4,
    'split':  5
}

VERBOSE = False
DEBUG = False

def INFO(msg: str) -> None:
    if VERBOSE:
        print(msg, file=sys.stderr)

def DEB(msg: str) -> None:
    if DEBUG:
        print(msg, file=sys.stderr)

def WARN(msg: str) -> None:
    print(msg, file=sys.stderr)

class asInteger(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, 1)

class CustomHelpFormatter(argparse.HelpFormatter):
    def _format_action_invocation(self, action):
        if not action.option_strings or action.nargs == 0:
            return super()._format_action_invocation(action)
        default = self._get_default_metavar_for_optional(action)
        args_string = self._format_args(action, default)
        return ', '.join(action.option_strings) + ' ' + args_string

class OptionsParser:
    def __init__(self):
        self.global_parser: argparse.ArgumentParser = None
        self.subparser: argparse._SubParsersAction = None
        self._init_global_parser()
        self._init_subparsers()

    def _init_global_parser(self) -> None:
        description = 'kmtricks cli'
        self.global_parser = argparse.ArgumentParser(
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            add_help=False
        )

        self.global_parser._positionals.title = 'Subcommands'
        self.global_parser._optionals.title = 'Global arguments'

        self.global_parser.add_argument('-v', '--verbose', action='store_true',
            help='Verbose mode'
        )

        self.global_parser.add_argument('-d', '--debug', action='store_true',
            help='Debug mode'
        )

        self.global_parser.add_argument('--version', action='version',
            version=f'kmtricks v{__version__}',
            help='Display kmtricks version')

        self.global_parser.add_argument('-h', '--help', action='help',
            help='Show this message and exit'
        )

    def _sub_env(self) -> None:
        desc = 'Build kmtricks runtime environment'
        subparser: argparse.ArgumentParser = self.subparser.add_parser(
            'env', description=desc,
            formatter_class=lambda prog: CustomHelpFormatter(prog, max_help_position=40, width=100), add_help=False
        )

        glb = subparser.add_argument_group('global')
        adv = subparser.add_argument_group('advanced performance tweaks')
        hmd = subparser.add_argument_group('hash mode configuration')

        glb.add_argument('--file', metavar='FILE', type=str,
            help='fof that contains path of read files, one per line',
            required=True)
        glb.add_argument('--run-dir', metavar='DIR', type=str,
            help='directory to write tmp and output files',
            required=True)
        glb.add_argument('--kmer-size', metavar='INT', type=int,
            help='size of a kmer', default=31)
        glb.add_argument('--abundance-min', metavar='INT', type=int,
            help='min abundance threshold for solid kmers', default=2)
        glb.add_argument('--abundance-max', metavar='INT', type=int,
            help='max abundance threshold for solid kmers', default=int(3e9))
        glb.add_argument('--max-memory', metavar='INT', type=int,
            help='max memory available in megabytes', default=8000)
        
        adv.add_argument('--minimizer-type', metavar='INT', type=int,
            help='minimizer type (0=lexi, 1=freq)', default=0)
        adv.add_argument('--minimizer-size', metavar='INT', type=int,
            help='size of minimizer', default=10)
        adv.add_argument('--repartition-type', metavar='INT', type=int,
            help='minimizer repartition (0=unordered, 1=ordered)', default=0)
        adv.add_argument('--nb-partitions', metavar='INT', type=int,
            help='number of partitions (0=auto)', default=0)
        
        hmd.add_argument('--hasher', metavar='STR', type=str,
            help='hash function: sabuhash, xor', default='xor')
        hmd.add_argument('--max-hash', metavar='INT', type=int,
            help='max hash value (0 < hash < max(int64))', default=int(1e9))

        glb.add_argument('-h', '--help', action='help',
            help='Show this message and exit')

    def _sub_run(self) -> None:
        desc = 'kmtricks pipeline'
        subparser: argparse.ArgumentParser = self.subparser.add_parser(
            'run', description=desc,
            formatter_class=lambda prog: CustomHelpFormatter(prog, max_help_position=40, width=100), add_help=False
        )

        glb = subparser.add_argument_group('global')
        ctr = subparser.add_argument_group('pipeline control')
        adv = subparser.add_argument_group('advanced performance tweaks')
        hmd = subparser.add_argument_group('hash mode configuration')

        mt_format = ['bin', 'ascii', 'pa', 'bf', 'bf_trp']
        steps = ['repart', 'superk', 'count', 'merge', 'split']

        glb.add_argument('--file', metavar='FILE', type=str,
            help='fof that contains path of read files, one per line',
            required=True)
        glb.add_argument('--run-dir', metavar='DIR', type=str,
            help='directory to write tmp and output files',
            required=True)
        glb.add_argument('--kmer-size', metavar='INT', type=int,
            help='size of a kmer', default=31)
        glb.add_argument('--abundance-min', metavar='INT', type=int,
            help='min abundance threshold for solid kmers', default=2)
        glb.add_argument('--abundance-max', metavar='INT', type=int,
            help='max abundance threshold for solid kmers', default=int(3e9))
        glb.add_argument('--recurrence-min', metavar='INT', type=int,
            help='min reccurence threshold for solid kmers', default=1)
        glb.add_argument('--max-memory', metavar='INT', type=int,
            help='max memory available in megabytes', default=8000)
        glb.add_argument('--mode', metavar='STR', type=str,
            choices=mt_format, default='bin',
            help=f'output matrix format: [{"|".join(mt_format)}]')
        glb.add_argument('--nb-cores', metavar='INT', type=int,
            help='number of cores', default=8)
        
        ctr.add_argument('--until', metavar='STR', type=str,
            choices=steps, default='all',
            help=f'run until step: [{"|".join(steps)}]')
        ctr.add_argument('--only', metavar='STR', type=str,
            choices=steps, default='all',
            help=f'run until step: [{"|".join(steps)}]')

        adv.add_argument('--minimizer-type', metavar='INT', type=int,
            help='minimizer type (0=lexi, 1=freq)', default=0)
        adv.add_argument('--minimizer-size', metavar='INT', type=int,
            help='size of minimizer', default=10)
        adv.add_argument('--repartition-type', metavar='INT', type=int,
            help='minimizer repartition (0=unordered, 1=ordered)', default=0)
        adv.add_argument('--nb-partitions', metavar='INT', type=int,
            help='number of partitions (0=auto)', default=0)
        
        hmd.add_argument('--hasher', metavar='STR', type=str,
            help='hash function: sabuhash, xor', default='xor')
        hmd.add_argument('--max-hash', metavar='INT', type=int,
            help='max hash value (0 < hash < max(int64))', default=int(1e9))
        hmd.add_argument('--split', metavar='STR', default='none',
            type=str, choices=['sdsl', 'howde', 'none'],
            help='split matrix in indidual files: [sdsl|howde] (only with -mf, --mode bf_trp)')

        glb.add_argument('--keep-tmp', action=asInteger,
            help='keep all tmp files', nargs=0, const=0, default=0)
        glb.add_argument('--lz4', action=asInteger,
            help='lz4 compression for tmp files', nargs=0, const=0, default=0)
        glb.add_argument('-h', '--help', action='help',
            help='Show this message and exit')


    def _get_subs(cls) -> List:
        return [getattr(cls, name) for name in dir(cls) if callable(getattr(cls, name)) and name.startswith('_sub')]

    def _init_subparsers(self) -> None:
        self.subcommands = list(map(lambda x: x.__name__.split('_')[-1], self._get_subs()))
        subcommands_str = ', '.join(self.subcommands)
        self.subparser = self.global_parser.add_subparsers(dest='cmd', metavar='cmd',
            help=f'{subcommands_str}'
        )

        self.subparsers = self._get_subs()
        for sub in self.subparsers:
            sub()

    def parse_args(self, as_dict: bool=True, arglist: list=None) -> Union[argparse.Namespace, Dict]:
        args = self.global_parser.parse_args(arglist)
        if args.cmd not in self.subcommands:
            self.global_parser.parse_args(['-h'])
        if as_dict:
            return vars(args)
        return args

class ICommand:
    def __init__(
        self, run_directory: str, cli_template: str, 
        args: dict, depends: set, idx: str, sync_id: str,
        log_path: str, wait: bool
    ):
        self.run_directory: str = run_directory 
        self.cli_template:  str = cli_template
        self.args:          dict = args
        self.p:             subprocess.Popen = None
        self.depends:       set = depends
        self.cores:         int = None
        self.idx:           str = idx
        self.wait:          bool = wait
        self.sync_id:       str = sync_id
        self.sf:            str = None
        self.log_path:      str = log_path
    
    @abc.abstractmethod
    def preprocess(self) -> None:
        pass
    
    @abc.abstractmethod
    def postprocess(self) -> None:
        pass

    def log_cmd(self, f: TextIO) -> None:
        f.write(f'{self.get_str_cmd()}\n')
        
    def get_str_cmd(self) -> str:
        return self.cli_template.format(**self.args)
    
    def run(self) -> None:
        self.preprocess()
        #self.p = subprocess.Popen('exec ' + self.get_str_cmd(), shell=True)
        if self.log_path:
            with open(self.log_path, 'w') as log_file:
                self.p = subprocess.Popen(
                    self.get_str_cmd().split(' '), stdout=log_file, stderr=subprocess.STDOUT
                )
        else:
            self.p = subprocess.Popen(self.get_str_cmd().split(' '))

        if self.wait:
            while not self.is_done:
                time.sleep(1) 

    def ready(self, finished: set) -> bool:
        if self.is_done:
            return False
        return self.depends.issubset(finished)
    
    def __lt__(self, that: "ICommand"):
        return self.idx < that.idx

    @property
    def sync_file(self):
        if self.sf:
            return os.path.exists(self.sf)
        return True
                

    @property
    def exit_code(self) -> Optional[int]:
        if self.is_done:
            return self.p.returncode
        return None
    
    @property
    def is_done(self) -> bool:
        if self.p:
            return self.p.poll() is not None
        return False

BIN_DIR = f'{os.path.dirname(os.path.abspath(__file__))}/bin'
if not os.path.exists(BIN_DIR):
    sys.exit("Unable to find kmtricks bin directory")
if not os.path.exists(f'{BIN_DIR}/km_configuration'):
    sys.exit(f"Unable to find kmtricks binaries at {BIN_DIR}")

BUILD_INFO = f'{os.path.dirname(os.path.abspath(__file__))}/build/build_infos.txt'

ENV_PREFIX_ID = 'E'
ENV_CLI_TEMPLATE = (
    f"{BIN_DIR}/km_configuration "
    "-file {file} "
    "-run-dir {run_dir} "
    "-abundance-min {abundance_min} "
    "-abundance-max {abundance_max} "
    "-kmer-size {kmer_size} "
    "-max-memory {max_memory} "
    "-minimizer-type {minimizer_type} "
    "-minimizer-size {minimizer_size} "
    "-repartition-type {repartition_type} "
    "-nb-parts {nb_partitions} "
    "-hasher {hasher} "
    "-max-hash {max_hash}"
)

REPART_PREFIX_ID = 'R'
LOG_PARTITIONER  = 'partitioner.log'
REPART_CLI_TEMPLATE = (
    f"{BIN_DIR}/km_minim_repart "
    "-file {file} "
    "-kmer-size {kmer_size} "
    "-run-dir {run_dir} "
    "-nb-cores {nb_cores}"
)

SUPERK_PREFIX_ID = 'S'
SUPERK_CLI_TEMPLATE = (
    f"{BIN_DIR}/km_reads_to_superk "
    "-file {f} "
    "-kmer-size {kmer_size} "
    "-run-dir {run_dir} "
    "-nb-cores {nb_cores}"
)

COUNT_PREFIX_ID = 'C'
COUNT_CLI_TEMPLATE = (
    f"{BIN_DIR}/km_superk_to_kmer_counts "
    "-file {f} "
    "-run-dir {run_dir} "
    "-abundance-min {abundance_min} "
    "-kmer-size {kmer_size} "
    "-part-id {part_id} "
    "-mode {mode} "
    "-keep-tmp {keep_tmp} "
    "-lz4 {lz4} "
    "-hasher {hasher} "
    "-max-hash {max_hash} "
    "-nb-cores {nb_cores}"
)

MERGE_PREFIX_ID = 'M'
MERGE_CLI_TEMPLATE = (
    f"{BIN_DIR}/km_merge_within_partition "
    "-run-dir {run_dir} "
    "-part-id {part_id} "
    "-abundance-min {abundance_min} "
    "-recurrence-min {recurrence_min} "
    "-mode {mode}"
)

OUTPUT_PREFIX_ID = 'O'
OUTPUT_CLI_TEMPLATE = (
    f"{BIN_DIR}/km_output_convert "
    "-run-dir {run_dir} "
    "-file {file} "
    "-nb-files {nb_files} "
    "-split {split} "
    "-kmer-size {kmer_size}"
)

progress_template = '\rRepartition: {R}/1, Superkmer: {S}/{SN}, Count: {C}/{CN}, Merge: {M}/{MN}, Output: {O}/{ON}'

ERROR_MSG = '\nSignal {sig} received from {cmd} with the following arguments:\n{args}.\n All children are killed. Check your inputs. If the problem persists, please contact us with a description of your run and the following files: {backtrace} and {build}.\n'

SIGNALS = (SIGABRT, SIGFPE, SIGILL, SIGINT, SIGSEGV, SIGTERM)

str_command = {
    'E': 'km_configuration',
    'R': 'km_minim_repart',
    'S': 'km_reads_to_superk',
    'C': 'km_superk_to_kmer_counts',
    'M': 'km_merge_within_partitions',
    'O': 'km_output_convert'
}

class EnvCommand(ICommand):
    def __init__(self, **kwargs):
        super().__init__(
            run_directory = kwargs['run_dir'],
            cli_template = ENV_CLI_TEMPLATE,
            args = kwargs,
            depends = None,
            idx = None,
            sync_id = None,
            wait = True,
            log_path = None
        )
        self.cores = 1

    def preprocess(self):
        if os.path.exists(self.run_directory):
            WARN(f'Warning: {self.run_directory} already exists.')
    
    def postprocess(self):
        pass

class RepartitionCommand(ICommand):
    def __init__(self, **kwargs):
        super().__init__(
            run_directory = kwargs['run_dir'],
            cli_template = REPART_CLI_TEMPLATE,
            args = kwargs,
            depends = {'E0'},
            idx = 'R',
            sync_id = 'R0',
            wait = True,
            log_path = kwargs['log']
        )
        self.cores = 1

    def preprocess(self) -> None:
        if not os.path.exists(self.run_directory):
            raise FileExistsError(f'{self.run_directory} doesn\'t exists.')
        if os.path.exists(f'{self.run_directory}/storage/partition_storage_gatb/minimRepart.minimRepart'):
            self.cli_template = 'echo {nb_cores} >> /dev/null 2>&1'
    
    def postprocess(self) -> None:
        pass

class SuperkCommand(ICommand):
    def __init__(self, **kwargs):
        deps = {REPART_PREFIX_ID + "0"} if kwargs['only'] != 'superk' else set()
        super().__init__(
            run_directory = kwargs['run_dir'],
            cli_template = SUPERK_CLI_TEMPLATE,
            args = kwargs,
            depends = deps,
            idx = 'S',
            sync_id = f'{SUPERK_PREFIX_ID}{kwargs["id"]}',
            wait = False,
            log_path = kwargs['log']
        )
        self.cores = 1
        self.args['nb_cores'] = self.cores

    def preprocess(self) -> None:
        repart_file = f'{self.run_directory}/storage/partition_storage_gatb/minimRepart.minimRepart'
        if not os.path.exists(repart_file):
            raise FileExistsError(f'{repart_file} doesn\'t exists.')
    
    def postprocess(self) -> None:
        pass

class CountCommand(ICommand):
    def __init__(self, **kwargs):
        deps = {SUPERK_PREFIX_ID + str(kwargs['fof'].get_id(kwargs['f']))} if kwargs['only'] != 'count' else set()
        super().__init__(
            run_directory = kwargs['run_dir'],
            cli_template = COUNT_CLI_TEMPLATE,
            args = kwargs,
            depends = deps,
            idx = 'C',
            sync_id = f'{COUNT_PREFIX_ID}_{kwargs["fof"].get_id(kwargs["f"])}_{kwargs["part_id"]}',
            wait = False,
            log_path = kwargs['log']
        )
        self.cores = 1
        self.args['nb_cores'] = self.cores

    def preprocess(self) -> None:
        f = self.args['f']
        superkstorage = f'{self.run_directory}/storage/superk_partitions/{os.path.basename(f)}'
        if not os.path.exists(f'{superkstorage}.superk'):
            raise FileExistsError(f'{superkstorage} doesn\'t exists.')
        
        pdir = f'{self.run_directory}/storage/kmers_partitions/partition_{self.args["part_id"]}'
        if os.listdir(pdir):
            raise FileExistsError (f'{pdir} already contains k-mer files')

    def postprocess(self) -> None:
        pass

class MergeCommand(ICommand):
    def __init__(self, **kwargs):
        deps = set()
        if kwargs['only'] != 'merge':
            for i in range(kwargs['fof'].nb):
                deps.add(f'{COUNT_PREFIX_ID}_{i}_{kwargs["part_id"]}')
        super().__init__(
            run_directory = kwargs['run_dir'],
            cli_template = MERGE_CLI_TEMPLATE,
            args = kwargs,
            depends = deps,
            idx = 'M',
            sync_id = f'{MERGE_PREFIX_ID}{kwargs["part_id"]}',
            wait = False,
            log_path = kwargs['log']
        )
        self.cores = 1
        self.args['nb_cores'] = self.cores

    def preprocess(self) -> None:
        p = self.args['part_id']
        pdir = f'{self.run_directory}/storage/kmers_partitions/partition_{p}'
        if not os.listdir(pdir):
            raise FileNotFoundError(f'{pdir} is empty')
        path = f'{pdir}/partition{p}.fof'
        ext = '.kmer' if not self.args['lz4'] else '.kmer.lz4'
        with open(path, 'w') as f_out:
            for _, f, _ in self.args['fof']:
                f_out.write(f'{pdir}/{os.path.basename(f)}{ext}\n')

    def postprocess(self) -> None:
        if not self.args['keep_tmp']:
            rmtree(f'{self.run_directory}/storage/kmers_partitions/partition_{self.args["part_id"]}')

class OutputCommand(ICommand):
    def __init__(self, **kwargs):
        deps = set()
        if kwargs['only'] != 'split':
            for i in range(kwargs['nb_partitions']):
                deps.add(f'M{i}')
        super().__init__(
            run_directory = kwargs['run_dir'],
            cli_template = OUTPUT_CLI_TEMPLATE,
            args = kwargs,
            depends = deps,
            idx = 'O',
            sync_id = 'O',
            wait = True,
            log_path = kwargs['log']
        )
        self.cores = 1
        self.args['nb_cores'] = self.cores

    def preprocess(self) -> None:
        pass

    def postprocess(self) -> None:
        if not self.args['keep_tmp']:
            for d in os.listdir(f'{self.run_directory}/storage/matrix'):
                if os.path.isdir(d):
                    rmtree(d)

class Timer():
    def __enter__(self):
        self.t1 = time.perf_counter()
        return self
    def __exit__(self, type, value, traceback):
        self.t2 = time.perf_counter()
        self.t = self.t2 - self.t1
    
    def print(self, template: str='{}', r: int=2) -> int:
        print(template.format(round(self.t, r)))
        return round(self.t, r)

class Progress():
    def __init__(self, pattern: str=''):
        self.pattern: str = pattern
        self.keys:    dict = ddict(int)
        
    def update(self, key: str=None):
        if key:
            self.keys[key] += 1
        print(f'\r{self.pattern.format_map(self.keys)}', end='', file=sys.stderr)

    def add(self, idx: str, nb:int):
        self.keys[idx] = 0
        self.keys[f'{idx}N'] = nb

class Fof:
    def __init__(self, path: str):
        self.path:    str = path
        self.fp:      TextIO = open(path, 'r')
        self.files:   odict = odict()
        self.is_read: bool = False
        self.id:      int = 0
        self.lfiles:  list = []
        self.nb:      int = 0
    
    def read(self, default_count: int=2) -> bool:
        self.fp.seek(0)
        has_ab = False
        for line in self.fp:
            line = line.rstrip()
            line_l = line.split(' ')
            if len(line_l) > 2:
                raise IOError(f'fof bad format: {self.fp.name}')
            elif len(line_l) == 2:
                path, count = line_l[0], int(line_l[1])
                has_ab = True
            else:
                path, count = ''.join(line_l), default_count
            self.files[path] = count
        self.lfiles = list(self.files)
        self.nb = len(self.lfiles)
        return has_ab

    def get_id(self, file: str):
        return self.lfiles.index(file)

    def copy(self, path: str) -> None:
        self.fp.seek(0)
        with open(path, 'w') as f_out:
            for line in self.fp:
                f_out.write(line)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            if self.id > self.nb:
                self.id = 0
            f = self.lfiles[self.id]
            return self.id, self.lfiles[self.id], self.files[f]
        except IndexError:
            raise StopIteration
        finally:
            self.id += 1

class Pool:
    def __init__(self, progress: Progress, log_cmd: str, nb_procs: int=8):
        self.procs:     int = nb_procs 
        self.available: int = int(self.procs)
        self.callable:  List[ICommand] = []
        self.finish:    Set[ICommand] = set()
        self.finish_id: Set[str] = set(['E0'])
        self.running:   Set[ICommand] = set()
        self.cmds:      List[Tuple[str, odict]] = []
        self.nb:        Dict[str, int] = {}
        self.count:     int = 1
        if log_cmd:
            self.log_cmd:   TextIO = open(log_cmd, 'a')
        self.progress:  Progress = progress

    def push(self, idx: str, cmds: odict) -> None:
        self.cmds.append((idx, cmds))
        self.nb[idx] = len(cmds)
        self.count += self.nb[idx]
        self.progress.add(idx, self.nb[idx])

    def exec(self) -> bool:
        self.progress.update()
        while len(self.finish_id) < self.count:
            if len(self.callable) < self.available:
                self.update_ready()
            if self.available and self.callable:
                self.run_ready()
            if self.running:
                self.check_finish()
                self.check_exit_code()
        self.check_finish()
        self.check_exit_code()

        print()
        return True

    def check_exit_code(self) -> None:
        while self.finish:
            cmd = self.finish.pop()
            ec = cmd.exit_code
            if ec in SIGNALS:
                print(ERROR_MSG.format(
                    sig=Signals(ec).name,
                    cmd=str_command[cmd.idx],
                    args=str(cmd.args),
                    backtrace='./km_backtrace/backtrace.log',
                    build=BUILD_INFO
                ))
                self.killall()
                sys.exit(1)
            else:
                cmd.postprocess()
                self.finish_id.add(cmd.sync_id)

    def update_ready(self) -> bool:
        update = False
        for _, pool_cmds in self.cmds:
            for k, cmd in copy(pool_cmds).items():
                if cmd.ready(self.finish_id):
                    update = True
                    heapq.heappush(self.callable, pool_cmds[k])
                    del pool_cmds[k]
        return update
    
    def run_ready(self) -> None:
        for cmd in copy(self.callable):
            cmd.run()
            self.available = max(self.available - cmd.cores, 0)
            self.running.add(cmd)
            self.callable.remove(cmd)
            if not self.available:
                break
    
    def check_finish(self) -> bool:
        finish = False
        for cmd in copy(self.running):
            if cmd.is_done and cmd.sync_file:
                finish = True
                self.log_cmd.write(cmd.get_str_cmd()+'\n')
                self.progress.update(cmd.idx)
                self.finish.add(cmd)
                self.available += cmd.cores
                self.running.remove(cmd)
        return finish

    def killall(self) -> int:
        for cmd in self.running:
            if cmd.p:
                cmd.p.kill()
        return 1

pool = Pool(Progress(), '')

def main():
    global VERBOSE, DEBUG
    
    cli = OptionsParser()
    args = cli.parse_args()
    VERBOSE, DEBUG = args['verbose'], args['debug']

    only = control[args['only']]
    until = control[args['until']]
    all_ = only == 6

    fof = Fof(args['file'])
    ab_per_file = fof.read(args['abundance_min'])
    
    if all_:
        env_cmd = EnvCommand(**args)
        env_cmd.run()

    if not args['nb_partitions']:
        args['nb_partitions'] = len(os.listdir(f'{args["run_dir"]}/storage/kmers_partitions'))

    log_dir = f'{args["run_dir"]}/logs'
    log_cmd_path = f'{log_dir}/cmds.log'

    progress_bar = Progress(progress_template)
    
    global pool
    pool = Pool(progress_bar, log_cmd_path, args['nb_cores'])

    if args['cmd'] == 'run':
        repart_commands = odict()
        if (only == 1 or all_):
            dargs = deepcopy(args)
            log = f'{log_dir}/repartition.log'
            repart_commands['R'] = RepartitionCommand(log=log, **dargs)
            pool.push('R', repart_commands)

        superk_commands = odict()
        if (only == 2 or all_ and until > 1):
            for i, f, _ in fof:
                dargs = deepcopy(args)
                log = f'{log_dir}/superk/superk_{i}.log'
                superk_commands[f'{SUPERK_PREFIX_ID}{i}'] = SuperkCommand(
                    id=i, f=f, fof=fof, log=log, **dargs
                )
            pool.push('S', superk_commands)

        count_commands = odict()
        if (only == 3 or all_ and until > 2):
            for i, f, c in fof:
                dargs = deepcopy(args)
                dargs['mode'] = mode_kh[dargs['mode']]
                if ab_per_file:
                    dargs['abundance_min'] = c
                for p in range(args['nb_partitions']):
                    log = f'{log_dir}/counter/counter{i}_{p}.log'
                    count_commands[f'{COUNT_PREFIX_ID}{i}_{p}'] = CountCommand(
                       f=f, part_id=p, fof=fof, log=log, **dargs
                    )
            pool.push('C', count_commands)

        merge_commands = odict()
        if (only == 4 or all_ and until > 3):
            for p in range(args['nb_partitions']):
                dargs = deepcopy(args)
                if ab_per_file:
                    dargs['abundance_min'] = 1
                log = f'{log_dir}/merger/merger{p}.log'
                merge_commands[f'{MERGE_PREFIX_ID}{p}'] = MergeCommand(
                    part_id=p, fof=fof, log=log, **dargs
                )
            pool.push('M', merge_commands)

        output_commands = odict()
        if ((only == 5 or all_ and until > 4) and args['mode'] == 'bf_trp'):
            dargs = deepcopy(args)
            log = f'{log_dir}/split.log'
            output_commands[f'{OUTPUT_PREFIX_ID}0'] = OutputCommand(nb_files=fof.nb, **dargs)
            pool.push('O', output_commands)

    with Timer() as total_time:
        pool.exec()
    
    total_time.print('Done in {} s.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nInterrupt signal received. All children are killed', file=sys.stderr)
        pool.killall()
        try:
            rmtree('./km_backtrace')
        except FileNotFoundError:
            pass
        sys.exit(1)
    except Exception as e:
        print(e, file=sys.stderr)
        pool.killall()
        sys.exit(1)
