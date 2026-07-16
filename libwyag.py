import argparse
import configparser
import hashlib
import os
import re
import subprocess
import sys
import zlib


parser = argparse.ArgumentParser(
    prog="wyag",
    description="The stupid content tracker",
)

argsubparsers = parser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True


def main(argv=sys.argv[1:]):
    args = parser.parse_args(argv)
    match args.command:
        case "init":
            cmd_init(args)
        case "cat-file":
            cmd_cat_file(args)
        case "hash-object":
            cmd_hash_object(args)
        case "unpack-packfile":
            cmd_unpack_packfile(args)
        case _:
            raise Exception(f"Bad command {args.command}.")


def default_config():
    ret = configparser.ConfigParser()
    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")
    return ret


class GitRepository(object):
    """A Git Repository"""

    worktree = None
    gitdir = None
    conf = None

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception(f"Not a Git repository {path}")

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
        """Same as repo_path, but create dirname(*path) if absent."""
        if self.repo_dir(*path[:-1], mkdir=mkdir):
            return self.repo_path(*path)
        return None

    def repo_dir(self, *path, mkdir=False):
        """Same as repo_path, but mkdir *path if absent if mkdir."""
        path = self.repo_path(*path)

        if os.path.exists(path):
            if os.path.isdir(path):
                return path
            raise Exception(f"Not a directory {path}")

        if mkdir:
            os.makedirs(path)
            return path
        return None


def repo_create(path):
    """Create a new repository at path."""
    repo = GitRepository(path, True)

    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f"{path} is not a directory!")
        if os.path.isdir(repo.gitdir):
            raise Exception(f"{path} is not empty!")
    else:
        os.makedirs(repo.worktree)

    repo.repo_dir("branches", mkdir=True)
    repo.repo_dir("objects", mkdir=True)
    repo.repo_dir("refs", "tags", mkdir=True)
    repo.repo_dir("refs", "heads", mkdir=True)

    with open(repo.repo_file("description"), "w", encoding="utf-8") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")

    with open(repo.repo_file("HEAD"), "w", encoding="utf-8") as f:
        f.write("ref: refs/heads/master\n")

    with open(repo.repo_file("config"), "w", encoding="utf-8") as f:
        config = default_config()
        config.write(f)

    return repo


def repo_find(path=".", required=True):
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    parent = os.path.realpath(os.path.join(path, ".."))
    if parent == path:
        if required:
            raise Exception("No git directory.")
        return None

    return repo_find(parent, required)


class GitObject(object):
    def __init__(self, data=None):
        if data is not None:
            self.deserialize(data)
        else:
            self.init()

    def serialize(self):
        raise Exception("Unimplemented!")

    def deserialize(self, data):
        raise Exception("Unimplemented!")

    def init(self):
        pass


class GitBlob(GitObject):
    fmt = b"blob"

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data


class _RawGitObject(GitObject):
    def serialize(self):
        return self.data

    def deserialize(self, data):
        self.data = data


class GitCommit(_RawGitObject):
    fmt = b"commit"


class GitTree(_RawGitObject):
    fmt = b"tree"


class GitTag(_RawGitObject):
    fmt = b"tag"


def object_read(repo, sha):
    path = repo.repo_file("objects", sha[0:2], sha[2:])
    if not path or not os.path.isfile(path):
        raise Exception(f"Object {sha} not found")

    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())

    x = raw.find(b" ")
    if x == -1:
        raise Exception(f"Malformed object {sha}: no space found")
    fmt = raw[0:x]
    y = raw.find(b"\x00", x+1)
    if y == -1:
        raise Exception(f"Malformed object {sha}: no null byte found")
    size = int(raw[x:y].decode("ascii"))
    if size != len(raw) - y - 1:
        raise Exception(f"Malformed object {sha}: bad length")

    match fmt:
        case b"commit":
            c = GitCommit
        case b"tree":
            c = GitTree
        case b"tag":
            c = GitTag
        case b"blob":
            c = GitBlob
        case _:
            raise Exception(f"Unknown object type {fmt.decode('ascii')} for object {sha}")

    return c(raw[y + 1 :])


def object_write(obj, repo=None):
    data = obj.serialize()
    result = obj.fmt + b" " + str(len(data)).encode() + b"\x00" + data
    sha = hashlib.sha1(result).hexdigest()

    if repo:
        path = repo.repo_file("objects", sha[0:2], sha[2:], mkdir=True)
        if path and not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(zlib.compress(result))

    return sha


def object_resolve(repo, name):
    candidates = []
    hash_re = re.compile(r"^[0-9A-Fa-f]{4,40}$")

    if not hash_re.match(name):
        return candidates

    name = name.lower()
    if len(name) == 40:
        return [name]

    prefix = name[:2]
    path = repo.repo_dir("objects", prefix)
    if not path:
        return candidates

    remainder = name[2:]
    for f in os.listdir(path):
        if f.startswith(remainder):
            candidates.append(prefix + f)
    return candidates


def object_find(repo, name, fmt=None, follow=True):
    del follow

    sha_list = object_resolve(repo, name)
    if not sha_list:
        raise Exception(f"No such reference {name}.")
    if len(sha_list) > 1:
        raise Exception(f"Ambiguous reference {name}: {', '.join(sha_list)}.")

    sha = sha_list[0]
    if fmt is None:
        return sha

    obj = object_read(repo, sha)
    if obj.fmt != fmt:
        raise Exception(f"Object {sha} is a {obj.fmt.decode('ascii')}, not a {fmt.decode('ascii')}.")
    return sha


def cat_file(repo, obj, fmt=None):
    obj = object_read(repo, object_find(repo, obj, fmt=fmt))
    sys.stdout.buffer.write(obj.serialize())


def object_hash(fd, fmt, repo=None):
    data = fd.read()

    match fmt:
        case b"commit":
            obj = GitCommit(data)
        case b"tree":
            obj = GitTree(data)
        case b"tag":
            obj = GitTag(data)
        case b"blob":
            obj = GitBlob(data)
        case _:
            raise Exception(f"Unknown type {fmt}!")

    return object_write(obj, repo)


def unpack_packfile(repo, packfile_path):
    packfile_abs = os.path.realpath(packfile_path)
    gitdir_abs = os.path.realpath(repo.gitdir)

    try: 
        if os.path.commonpath([packfile_abs, gitdir_abs]) == gitdir_abs:
            raise Exception("Packfile must be outside .git directory.")
    except ValueError:
        pass
    if not os.path.isfile(packfile_abs):
        raise Exception(f"Packfile not found: {packfile_path}")

    with open(packfile_abs, "rb") as pf:
        proc = subprocess.run(
            ["git", "unpack-objects"],
            cwd=repo.worktree,
            stdin=pf,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    if proc.returncode != 0:
        raise Exception(proc.stderr.decode("utf-8", errors="replace").strip() or "git unpack-objects failed")

    return proc.stdout.decode("utf-8", errors="replace")


argsp = argsubparsers.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument("path", metavar="directory", nargs="?", default=".", help="Where to create the repository.")


def cmd_init(args):
    repo_create(args.path)


argsp = argsubparsers.add_parser("cat-file", help="Provide content of repository objects")
argsp.add_argument("type", metavar="type", choices=["blob", "commit", "tag", "tree"], help="Specify the type")
argsp.add_argument("object", metavar="object", help="The object to display")


def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, fmt=args.type.encode())


argsp = argsubparsers.add_parser(
    "hash-object", help="Compute object ID and optionally creates an object from a file"
)
argsp.add_argument(
    "-t",
    metavar="type",
    dest="type",
    choices=["blob", "commit", "tag", "tree"],
    default="blob",
    help="Specify the type",
)
argsp.add_argument("-w", dest="write", action="store_true", help="Actually write the object into the database")
argsp.add_argument("path", help="Read object from <file>")


def cmd_hash_object(args):
    repo = repo_find() if args.write else None
    with open(args.path, "rb") as fd:
        sha = object_hash(fd, args.type.encode(), repo)
    print(sha)


argsp = argsubparsers.add_parser(
    "unpack-packfile",
    help="Read a .pack file from the worktree and unpack objects into .git/objects",
)
argsp.add_argument("packfile", help="Path to .pack file (must be outside .git)")


def cmd_unpack_packfile(args):
    repo = repo_find()
    packfile_path = args.packfile if os.path.isabs(args.packfile) else os.path.join(repo.worktree, args.packfile)
    out = unpack_packfile(repo, packfile_path)
    if out:
        print(out, end="")

def kvlm_parse(raw, start=0, dct=None):
    if not dct:
        dct = dict()
    spc = raw.find(b" ", start)
    nl = raw.find(b"\n", start)
    if spc < 0 or nl < spc:
        assert nl == start
        dct[b""] = raw[start + 1 :]
        return dct
    
    key = raw[start:spc]
    end = start
    while True:
        end = raw.find(b"\n", end + 1)
        if raw[end + 1] != ord(" "):
            break
    
    value = raw[spc + 1 : end].replace(b"\n ", b"\n")
    if key in dct:
        if type(dct[key]) == list:
            dct[key].append(value)
        else:
            dct[key] = [dct[key], value]
    else:
        dct[key] = value
    
    return kvlm_parse(raw, start=end + 1, dct=dct)

def kvlm_serialize(kvlm):
    ret = b''

    for k in kvlm.keys():
        if k == None: continue
        val = kvlm[k]
        if type(val) != list:
             val = [val]

        for v in val:
            ret+= k+b' '+ (v.replace(b'\n', b'\n')) + b'\n'
    
    ret += b'\n' + kvlm[None]
    return ret

class GitCommit(GitObject):
    fmt=b'commit'

    def deserialize(self, data):
        self.kvlm = kvlm_parse(data)

    def serialize(self):
        return kvlm_serialize(self.kvlm)

    def init(self):
        self.kvlm = dict()

argsp = argsubparsers.add_parser("log", help="Display history of a given commit.")
argsp.add_argument("commit",
                   default="HEAD",
                   nargs="?",
                   help="Commit to start at.")

def cmd_log(args):
    repo = repo_find()

    print("digraph wyaglog{")
    print("  node[shape=rect]")
    log_graphviz(repo, object_find(repo, args.commit), set())
    print("}")

def log_graphviz(repo, sha, seen):

    if sha in seen:
        return
    seen.add(sha)

    commit = object_read(repo, sha)
    message = commit.kvlm[None].decode("utf8").strip()
    message = message.replace("\\", "\\\\")
    message = message.replace("\"", "\\\"")

    if "\n" in message: # Keep only the first line
        message = message[:message.index("\n")]

    print(f"  c_{sha} [label=\"{sha[0:7]}: {message}\"]")
    assert commit.fmt==b'commit'

    if not b'parent' in commit.kvlm.keys():
        # Base case: the initial commit.
        return

    parents = commit.kvlm[b'parent']

    if type(parents) != list:
        parents = [ parents ]

    for p in parents:
        p = p.decode("ascii")
        print (f"  c_{sha} -> c_{p};")
        log_graphviz(repo, p, seen)