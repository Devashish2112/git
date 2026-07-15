import argparse
import configparser
import hashlib
import os
import sys
import re
import zlib

from datetime import datetime
from fnmatch import fnmatch
from math import ceil

try: 
    import grp, pwd
except ModuleNotFoundError:
    pass

parser = argparse.ArgumentParser(
                    prog='ProgramName',
                    description='What the program does',
                    epilog='Text at the bottom of help')

argsubparser = parser.add_subparsers(title="Commands", dest="command")
argsubparser.required = True

def main(argv=sys.argv[1:]):
    args = parser.parse_args(argv)
    match args.command:
        case "add"          : cmd_add(args)
        case "cat-file"     : cmd_cat_file(args)
        case "check-ignore" : cmd_check_ignore(args)
        case "checkout"     : cmd_checkout(args)
        case "commit"       : cmd_commit(args)
        case "hash-object"  : cmd_hash_object(args)
        case "init"         : cmd_init(args)
        case "log"          : cmd_log(args)
        case "ls-files"     : cmd_ls_files(args)
        case "ls-tree"      : cmd_ls_tree(args)
        case "rev-parse"    : cmd_rev_parse(args)
        case "rm"           : cmd_rm(args)
        case "show-ref"     : cmd_show_ref(args)
        case "status"       : cmd_status(args)
        case "tag"          : cmd_tag(args)
        case _              : print("Bad command.")


def default_config(): 
    ret = configparser.ConfigParser()
    
    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")
    
    return ret


def repo_create(path):
    """Create a new repository at path."""
    repo = GitRepository(path, True)
    
    # First, we make sure the path either doesn't exist or is empty.
    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f"{path} is not a directory!")
        if os.listdir(repo.worktree):
            raise Exception(f"{path} is not empty!")
    else:
        os.makedirs(repo.worktree)
    
    assert repo.repo_dir("branches", mkdir=True)
    assert repo.repo_dir("objects", mkdir=True)
    assert repo.repo_dir("refs", "tags", mkdir=True)
    assert repo.repo_dir("refs", "heads", mkdir=True)
    
    # .git/description
    with open(repo.repo_file("description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")
    
    # .git/HEAD
    with open(repo.repo_file("HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")
    
    # .git/config
    with open(repo.repo_file("config"), "w") as f:
        config = default_config()
        config.write(f)
    
    return repo


def cmd_init(args):
    repo_create(args.path)
        
class GitRepository (object):
    """A Git Repository"""
    
    worktree = None
    gitdir = None
    conf = None
    
    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")
        
        if not (force or os.path.isdir(self.gitdir)):
            raise Exception(f"Not a Git repository {path}")
        
        # Read configuration file in .git/config
        self.conf = configparser.ConfigParser()
        cf = self.repo_file("config")
        
        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")
        
        if not force:
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception(f"Unsupported repositoryformatversion {vers}")
            
    def repo_path(self, *path):
        """Compute path under repo's gitdir."""
        return os.path.join(self.gitdir, *path)
    
    def repo_file(self, *path, mkdir=False):
        """Same as repo_path, but create dirname(*path) if absent.  For
        example, repo_file(r, \"refs\", \"remotes\", \"origin\", \"HEAD\") will create
        .git/refs/remotes/origin."""
        if self.repo_dir(*path[:-1], mkdir=mkdir):
            return self.repo_path(*path)

    def repo_dir(self, *path, mkdir=False):
        """Same as repo_path, but mkdir *path if absent if mkdir."""
        path = self.repo_path(*path)
        
        if os.path.exists(path):
            if (os.path.isdir(path)):
                return path
            else:
                raise Exception(f"Not a directory {path}")
        
        if mkdir:
            os.makedirs(path)
            return path
        else:
            return None
        

argsp = argsubparser.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument("path", metavar="directory", nargs="?", default=".", help="Where to create the repository.")
    