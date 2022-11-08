#!/QOpenSys/pkgs/bin/python3.6

# Licensed Materials - Property of IBM
# 57XX-XXX
# (c) Copyright IBM Corp. 2021
""" The module used to build a project"""
import sys
from pathlib import Path
from tempfile import mkstemp
from typing import Any, Dict, List, Optional
from makei.const import BOB_PATH
from makei.ibmi_json import IBMiJson
from makei.iproj_json import IProjJson
from makei.rules_mk import RulesMk
from makei.utils import objlib_to_path, \
    run_command, support_color, print_to_stdout, Colors, colored


class BuildEnv():
    """ The Build Environment used to build or compile a project. """

    color_tty: bool
    src_dir: Path
    targets: List[str]
    make_options: Optional[str]
    bob_path: Path
    bob_makefile: Path
    build_vars_path: Path
    build_vars_handle: Path
    curlib: str
    pre_usr_libl: str
    post_usr_libl: str
    iproj_json_path: Path
    iproj_json: IProjJson
    ibmi_env_cmds: str

    tmp_files: List[Path] = []
    target_maps: Dict[str, str]
    success_targets: List[str]
    failed_targets: List[str]

    def __init__(self, targets: List[str] = None, srcs: List[str] = None, make_options: Optional[str] = None,
                overrides: Dict[str, Any] = None):
        overrides = overrides or {}
        self.src_dir = Path.cwd()
        self.make_options = make_options if make_options else ""
        self.bob_path = Path(
            overrides["bob_path"]) if "bob_path" in overrides else BOB_PATH
        self.bob_makefile = self.bob_path / 'mk' / 'Makefile'
        self.build_vars_handle, path = mkstemp()
        self.build_vars_path = Path(path)
        self.iproj_json_path = self.src_dir / "iproj.json"
        self.iproj_json = IProjJson.from_file(self.iproj_json_path)
        self.color = support_color()

        if len(self.iproj_json.set_ibm_i_env_cmd) > 0:
            cmd_list = self.iproj_json.set_ibm_i_env_cmd
            self.ibmi_env_cmds = "\\n".join(cmd_list)
        else:
            self.ibmi_env_cmds = ""

        self.success_targets = []
        self.failed_targets = []

        self.target_maps = self._read_rules_mks()
        if targets:
            self.targets = targets
        elif srcs:
            self.targets = [self.target_maps[src] for src in srcs]
        else:
            self.targets = ["all"]

        self._create_build_vars()

    def __del__(self):
        self.build_vars_path.unlink()

    def generate_make_cmd(self):
        """ Returns the make command used to build the project."""
        cmd = f'/QOpenSys/pkgs/bin/make -k BUILDVARSMKPATH="{self.build_vars_path}"' + \
            f' -k BOB="{self.bob_path}" -f "{self.bob_makefile}"'
        if self.make_options:
            cmd = f"{cmd} {self.make_options}"
        cmd = f"{cmd} {' '.join(self.targets)}"
        return cmd

    def _read_rules_mks(self):
        rules_mks = [self.src_dir / "Rules.mk"]
        map_to_targets = {}
        while (len(rules_mks) > 0):
            rules_mk = rules_mks.pop()
            if rules_mk.exists():
                rules_mk = RulesMk.from_file(rules_mk)
                for target, src_list in rules_mk.targets:
                    for src in src_list:
                        map_to_targets[src] = target
                
                map_to_targets[rules_mk.containing_dir.name] = "dir_" + rules_mk.containing_dir.name
                rules_mks.extend([self.src_dir / subdir / "Rules.mk" for subdir in rules_mk.subdirs])
        return map_to_targets

    def _create_build_vars(self):
        target_file_path = self.build_vars_path

        rules_mk_paths = list(Path(".").rglob("Rules.mk"))
        # Create Rules.mk.build for each Rules.mk
        for rules_mk_path in rules_mk_paths:
            rules_mk = RulesMk.from_file(rules_mk_path, map(Path, self.iproj_json.include_path))
            rules_mk.build_context = self
            rules_mk_build_path = rules_mk_path.parent / ".Rules.mk.build"
            rules_mk_build_path.write_text(str(rules_mk))
            self.tmp_files.append(rules_mk_build_path)

        subdirs = list(map(lambda x: x.parents[0], rules_mk_paths))

        subdirs.sort(key=lambda x: len(x.parts))
        dir_var_map = {Path('.'): IBMiJson.from_values(self.iproj_json.tgt_ccsid, self.iproj_json.objlib)}

        def map_ibmi_json_var(path):
            if path != Path("."):
                dir_var_map[path] = IBMiJson.from_file(path / ".ibmi.json", dir_var_map[path.parents[0]])

        list(map(map_ibmi_json_var, subdirs))

        # set build env variables based on iproj.json
        # if not include_path specified just use INCDIR(*NONE)
        #  otherwise use INCDIR('dir1' 'dir2')
        incdir ="*NONE";
        includePath = self.iproj_json.include_path;
        # if include path is not empty or *NONE then wrap in single quotes
        if len(includePath) > 0 and [v.upper() for v in includePath] != ["*NONE"]:
            incdir='\''+'\' \''.join(includePath)+'\''
        with target_file_path.open("w", encoding="utf8") as file:
            file.write(f"""# This file is generated by makei, DO NOT EDIT.
# Modify .ibmi.json to override values

curlib := {self.iproj_json.curlib}
preUsrlibl := {' '.join(self.iproj_json.pre_usr_libl)}
postUsrlibl := {' '.join(self.iproj_json.post_usr_libl)}
INCDIR := {incdir}
unquotedINCDIR := {' '.join(includePath)}
doublequotedINCDIR := {incdir.replace("'", "''")}
IBMiEnvCmd := {self.ibmi_env_cmds}
COLOR_TTY := {'true' if self.color else 'false'}

""")        
            for subdir in subdirs:
                # print(dir_var_map[subdir].build)
                file.write(
                    f"TGTCCSID_{subdir.absolute()} := {dir_var_map[subdir].build['tgt_ccsid']}\n")
                file.write(
                    f"OBJPATH_{subdir.absolute()} := {objlib_to_path(dir_var_map[subdir].build['objlib'])}\n")

            # for rules_mk in rules_mks:
            #     with rules_mk.open('r') as rules_mk_file:
            #         lines = rules_mk_file.readlines()
            #         for line in lines:
            #             line = line.rstrip()
            #             if line and not line.startswith("#") \
            #                     and not "=" in line and not line.startswith((' ', '\t')):
            #                 file.write(
            #                     f"{line.split(':')[0]}_d := {rules_mk.parents[0].absolute()}\n")

    def make(self):
        """ Generate and execute the make command."""
        if (self.src_dir / ".logs" / "joblog.json").exists():
            (self.src_dir / ".logs" / "joblog.json").unlink()
        if (self.src_dir / ".logs" / "output.log").exists():
            (self.src_dir / ".logs" / "output.log").unlink()


        def handle_make_output(lineBytes: bytes):
            if type(lineBytes) == bytes:
                line = lineBytes.decode(sys.getdefaultencoding())
            if "Failed to create" in line:
                self.failed_targets.append(line.split()[-1].split("!")[0])
            if "was created successfully!" in line:
                self.success_targets.append(line.split()[1])
            print_to_stdout(line)

        run_command(self.generate_make_cmd(), handle_make_output)
        self._post_make()
        return not self.failed_targets


    def _post_make(self):
        for tmp_file in self.tmp_files:
            tmp_file.unlink()
        print(colored(f"Objects:            ", Colors.BOLD), colored(f"{len(self.failed_targets)} failed", Colors.FAIL), colored(f"{len(self.success_targets)} succeed", Colors.OKGREEN), f"{len(self.success_targets)+len(self.failed_targets)} total")
        if self.failed_targets:
            print(f" > Failed objects:   ", " ".join(self.failed_targets))
        print(colored(f"Build Completed!", Colors.BOLD))
        # event_files = list(Path(".evfevent").rglob("*.evfevent"))

        # def replace_abs_path(line: str) -> str:
        #     if str(Path.cwd()) in line:
        #         line = line.replace(f'{Path.cwd()}/', '')
        #         new_len = len(line.split()[5])
        #         # Replace length
        #         line = line[:24] + f"{new_len:03d}" + line[27:]
        #         return line
        #     else:
        #         return line

        # for filepath in event_files:
        #     replace_file_content(filepath, replace_abs_path)