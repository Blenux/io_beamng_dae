"""BeamNG Virtual File System — Python port of the fork's io/bng_vfs.

Unified file access over disk directories and .zip archives (vanilla
BeamNG.drive packaging). Mounts are searched in reverse order — last
mounted wins, so user content overrides game content and mods override
both (PhysFS semantics, fork bng_vfs.cc::resolve).

VFS paths are BeamNG-style: "/vehicles/vivace/vivace_main_b.color.png".
Image loading needs bpy; everything else is plain Python.
"""

import os
import tempfile
import zipfile


_EXT_FALLBACK = (".dds", ".DDS", ".Dds")


def _norm(path):
    return path.replace("\\", "/")


def _ext_variants(vfs_path):
    """Fork's ext_fallback_variants: .png prefers .dds on disk/zip,
    .dds falls back to .png."""
    low = vfs_path.lower()
    if low.endswith(".png"):
        base = vfs_path[:-4]
        return [base + e for e in _EXT_FALLBACK] + [vfs_path]
    if low.endswith(".dds"):
        return [vfs_path, vfs_path[:-4] + ".png"]
    return [vfs_path]


class _Mount:
    __slots__ = ("fs_path", "is_zip", "zip_prefix", "vfs_prefix",
                 "_zip", "_members")

    def __init__(self, fs_path, vfs_prefix, zip_prefix, is_zip):
        self.fs_path = fs_path
        self.vfs_prefix = vfs_prefix.rstrip("/")
        self.zip_prefix = zip_prefix.strip("/")
        self.is_zip = is_zip
        self._zip = None
        self._members = None  # lowercase member → member

    def zip(self):
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.fs_path)
        return self._zip

    def members(self):
        if self._members is None:
            self._members = {n.lower(): n for n in self.zip().namelist()
                             if not n.endswith("/")}
        return self._members

    def find_member(self, rel):
        """rel → actual zip member name (case-insensitive) or None."""
        internal = (self.zip_prefix + "/" + rel).strip("/")
        return self.members().get(internal.lower())

    def member_children(self, rel_dir):
        """Immediate children of zip_prefix/rel_dir → (names, dirs)."""
        prefix = (self.zip_prefix + "/" + rel_dir).strip("/")
        prefix = prefix + "/" if prefix else ""
        files, dirs = set(), set()
        for member in self.zip().namelist():
            if not member.startswith(prefix) or member == prefix:
                continue
            rest = member[len(prefix):]
            head, _, tail = rest.partition("/")
            (dirs if tail else files).add(head)
        return files, dirs

    def close(self):
        if self._zip is not None:
            self._zip.close()
            self._zip = None
            self._members = None


class _Resolved:
    __slots__ = ("mount", "rel", "member", "variant")

    def __init__(self, mount, rel, member, variant):
        self.mount = mount
        self.rel = rel          # path relative to the mount
        self.member = member    # zip member name (zip mounts only)
        self.variant = variant  # matched VFS path (may be ext-fallback)

    @property
    def is_zip(self):
        return self.member is not None

    @property
    def fs_path(self):
        return os.path.join(self.mount.fs_path, *self.rel.split("/"))


class BNGVfs:
    """Port of bng_vfs::BNGVfs — global instance via vfs_get_global()."""

    def __init__(self):
        self.mounts = []
        self._read_cache = {}

    # -- mounts --

    def mount(self, fs_path, vfs_prefix, zip_prefix=""):
        """Mount a directory or .zip. Returns True on success."""
        if not fs_path or not vfs_prefix:
            return False
        is_zip = fs_path.lower().endswith(".zip")
        if is_zip:
            if not os.path.isfile(fs_path):
                return False
        elif not os.path.isdir(fs_path):
            return False
        self.mounts.append(_Mount(fs_path, vfs_prefix, zip_prefix, is_zip))
        return True

    def clear(self):
        for m in self.mounts:
            m.close()
        self.mounts = []
        self._read_cache.clear()

    def has_mounts(self):
        return bool(self.mounts)

    # -- resolution --

    def _resolve(self, vfs_path):
        """VFS path → _Resolved or None. Last-mounted wins; each variant
        is tried against all mounts before the next variant (fork order)."""
        if not vfs_path:
            return None
        normalized = _norm(vfs_path)
        for variant in _ext_variants(normalized):
            for mount in reversed(self.mounts):
                prefix = mount.vfs_prefix + "/"
                v = variant.lstrip("/")
                mp = prefix.lstrip("/")
                if not v.startswith(mp):
                    continue
                rel = v[len(mp):]
                if not rel:
                    continue
                if mount.is_zip:
                    member = mount.find_member(rel)
                    if member is not None:
                        return _Resolved(mount, rel, member, variant)
                elif os.path.isfile(
                        os.path.join(mount.fs_path, *rel.split("/"))):
                    return _Resolved(mount, rel, None, variant)
        return None

    def file_exists(self, vfs_path):
        return self._resolve(vfs_path) is not None

    def resolve_path(self, vfs_path):
        """Resolved filesystem path (disk mounts) or 'zip:member' — debug."""
        r = self._resolve(vfs_path)
        if r is None:
            return ""
        return (r.mount.fs_path + ":" + r.member) if r.is_zip else r.fs_path

    # -- reads --

    def read_file(self, vfs_path):
        """File bytes (cached after first read, like the game)."""
        cached = self._read_cache.get(vfs_path)
        if cached is not None:
            return cached
        r = self._resolve(vfs_path)
        if r is None:
            return b""
        try:
            if r.is_zip:
                data = r.mount.zip().read(r.member)
            else:
                with open(r.fs_path, "rb") as f:
                    data = f.read()
        except (OSError, KeyError, zipfile.BadZipFile):
            return b""
        self._read_cache[vfs_path] = data
        self._read_cache[r.variant] = data
        return data

    def read_file_text(self, vfs_path):
        data = self.read_file(vfs_path)
        return data.decode("utf-8", "replace") if data else ""

    # -- listing --

    def list_directory(self, vfs_dir):
        """Merged children of vfs_dir across mounts.
        Returns [(name, vfs_path, is_directory)] — later mounts win."""
        vfs_dir = _norm(vfs_dir).rstrip("/")
        files, dirs = {}, {}
        for mount in self.mounts:  # forward: later mounts overwrite
            prefix = mount.vfs_prefix + "/"
            v = vfs_dir.lstrip("/") + "/"
            mp = prefix.lstrip("/")
            if not v.startswith(mp):
                continue
            rel = v[len(mp):].rstrip("/")
            if mount.is_zip:
                try:
                    f, d = mount.member_children(rel)
                except zipfile.BadZipFile:
                    continue
            else:
                fs_dir = os.path.join(mount.fs_path, *rel.split("/")) \
                    if rel else mount.fs_path
                try:
                    entries = list(os.scandir(fs_dir))
                except OSError:
                    continue
                f = {e.name for e in entries if e.is_file()}
                d = {e.name for e in entries if e.is_dir()}
            for n in f:
                files[n] = vfs_dir + "/" + n
                dirs.pop(n, None)
            for n in d:
                dirs[n] = vfs_dir + "/" + n
        return ([(n, p, False) for n, p in sorted(files.items())]
                + [(n, p, True) for n, p in sorted(dirs.items())])

    # -- images --

    def load_image(self, vfs_path):
        """Resolve + load into a bpy.types.Image (colorspace by suffix).

        Disk mounts load directly (fork's BKE_image_load_exists path);
        zip members extract to a temp cache then load (fork's
        IMB_load_image_from_memory equivalent — Blender's Python API
        can't decode from memory, so the extracted file stands in).
        """
        import bpy  # deferred — bng_vfs stays usable outside Blender
        from .materials_library import is_srgb_texture

        r = self._resolve(vfs_path)
        if r is None:
            return None
        if r.is_zip:
            try:
                data = r.mount.zip().read(r.member)
            except (KeyError, zipfile.BadZipFile):
                return None
            import zlib
            cache_dir = os.path.join(tempfile.gettempdir(), "bng_vfs_tex")
            os.makedirs(cache_dir, exist_ok=True)
            key = "%08x_%s" % (
                zlib.crc32(r.mount.fs_path.encode()) & 0xffffffff,
                r.variant.replace("/", "_"))
            cache_path = os.path.join(cache_dir, key)
            if not os.path.isfile(cache_path):
                try:
                    with open(cache_path, "wb") as f:
                        f.write(data)
                except OSError:
                    return None
            fs_path = cache_path
        else:
            fs_path = r.fs_path
        try:
            img = bpy.data.images.load(fs_path, check_existing=True)
        except RuntimeError:
            return None
        try:
            img.colorspace_settings.name = (
                'sRGB' if is_srgb_texture(r.variant) else 'Non-Color')
        except TypeError:
            pass
        return img


# ---------------------------------------------------------------------------
# Zip prefix auto-detection + global instance
# ---------------------------------------------------------------------------

def detect_zip_prefix(zip_path, vehicle_name=""):
    """BeamNG vehicle zips contain 'vehicles/<name>/'; mod zips 'vehicles/';
    plain zips may be rooted at '<name>/' or the zip root."""
    try:
        names = zipfile.ZipFile(zip_path).namelist()
    except (OSError, zipfile.BadZipFile):
        return ""
    if vehicle_name and any(
            n.startswith("vehicles/" + vehicle_name + "/") for n in names):
        return "vehicles/" + vehicle_name
    if any(n.startswith("vehicles/") for n in names):
        return "vehicles"
    if vehicle_name and any(
            n.startswith(vehicle_name + "/") for n in names):
        return vehicle_name
    return ""


_vfs = BNGVfs()


def vfs_get_global():
    """Fork's vfs_get_global() singleton."""
    return _vfs


# ---------------------------------------------------------------------------
# Import-time auto-mount (fork's bng_vfs_mount_all_sources)
# ---------------------------------------------------------------------------

def _vehicle_candidates(filepath, material_names):
    """Vehicle-name guesses: the file's basename plus the first token of
    each material name ('vivace_mechanical' → 'vivace')."""
    cands = [os.path.splitext(os.path.basename(filepath))[0]]
    for name in material_names or ():
        tok = name.split("_")[0] if name else ""
        if tok and tok not in cands:
            cands.append(tok)
    return cands


def _mount_zip(vfs, zpath, veh_name=""):
    """Mount a zip at the VFS prefix matching its internal layout."""
    prefix = detect_zip_prefix(zpath, veh_name)
    stem = veh_name or os.path.splitext(os.path.basename(zpath))[0]
    if prefix == "vehicles":
        vfs.mount(zpath, "/vehicles", "vehicles")
    else:
        vfs.mount(zpath, "/vehicles/" + stem, prefix)


def auto_mount(filepath, vehicle_dir, material_names=(),
               game_vehicles_dir="", user_dir=""):
    """Mount every source in fork priority order (last-mounted wins):
    candidate game zips → common.zip → game vehicles dir → user vehicles
    → user common → mod zips → unpacked mods → local sibling zips →
    local vehicles root → the vehicle dir itself.

    Returns the detected VFS vehicle dir ("/vehicles/<name>") or "".
    """
    import glob

    vfs = vfs_get_global()
    vfs.clear()
    cands = _vehicle_candidates(filepath, material_names)

    # 1. Game vehicle zips for the candidate names + common.zip.
    if game_vehicles_dir and os.path.isdir(game_vehicles_dir):
        for c in cands:
            z = os.path.join(game_vehicles_dir, c + ".zip")
            if os.path.isfile(z):
                vfs.mount(z, "/vehicles/" + c,
                          detect_zip_prefix(z, c) or "vehicles/" + c)
        common_zip = os.path.join(game_vehicles_dir, "common.zip")
        if os.path.isfile(common_zip):
            vfs.mount(common_zip, "/vehicles/common", "vehicles/common")
        # 2. Extracted game vehicles dir shadows its zips.
        vfs.mount(game_vehicles_dir, "/vehicles")

    # 3. User path vehicles + common override game content.
    if user_dir:
        uveh = os.path.join(user_dir, "vehicles")
        vfs.mount(uveh, "/vehicles")
        ucommon = os.path.join(uveh, "common")
        if os.path.isdir(ucommon):
            vfs.mount(ucommon, "/vehicles/common")
        # 4. Mod zips + unpacked mods override everything game-side.
        mods = os.path.join(user_dir, "mods")
        if os.path.isdir(mods):
            for e in sorted(os.listdir(mods)):
                p = os.path.join(mods, e)
                if e.lower().endswith(".zip") and os.path.isfile(p):
                    _mount_zip(vfs, p)
            unpacked = os.path.join(mods, "unpacked")
            if os.path.isdir(unpacked):
                for e in sorted(os.listdir(unpacked)):
                    mv = os.path.join(unpacked, e, "vehicles")
                    if os.path.isdir(mv):
                        vfs.mount(mv, "/vehicles")

    # 5. Local context — highest priority (extracted working copies).
    vdir = (os.path.normpath(vehicle_dir) if vehicle_dir
            else os.path.dirname(filepath))
    vroot = os.path.dirname(vdir)
    if vroot and os.path.isdir(vroot):
        for z in sorted(glob.glob(os.path.join(vroot, "*.zip"))):
            _mount_zip(vfs, z, os.path.splitext(os.path.basename(z))[0])
        vfs.mount(vroot, "/vehicles")
    if vdir and os.path.isdir(vdir):
        vfs.mount(vdir, "/vehicles/" + os.path.basename(vdir))
    base_zip = os.path.splitext(filepath)[0] + ".zip"
    if os.path.isfile(base_zip):
        _mount_zip(vfs, base_zip, os.path.splitext(
            os.path.basename(filepath))[0])

    # The vehicle's VFS root — the first candidate with materials.json.
    for c in cands:
        if vfs.file_exists("/vehicles/" + c + "/main.materials.json"):
            return "/vehicles/" + c
    return ""
