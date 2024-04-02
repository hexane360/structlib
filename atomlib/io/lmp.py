
from ..atoms import HasAtoms
from ..cell import HasCell
from ..util import open_file, FileOrPath, localtime


def write_lmp(atoms: HasAtoms, f: FileOrPath):
    with open_file(f, 'w') as f:
        def p(s: object):
            print(s, file=f)

        now = localtime()
        p(f"# Generated by atomlib on {now.isoformat(' ', 'seconds')}\n")

        frame = atoms.get_atoms('local').with_type()

        types = frame.unique(subset='type')
        types = types.with_mass().sort('type')

        p(f" {len(frame):8} atoms")
        p(f" {len(types):8} atom types\n")

        if isinstance(atoms, HasCell):
            if not atoms.is_orthogonal_in_local():
                raise NotImplementedError()  # triclinic output not yet supported (https://docs.lammps.org/Howto_triclinic.html)
            bbox = atoms.bbox_cell()
        else:
            bbox = atoms.bbox_atoms()

        for (s, (low, high)) in zip(('x', 'y', 'z'), (bbox.x, bbox.y, bbox.z)):
            p(f" {low:16.7f} {high:14.7f} {s}lo {s}hi")

        p(f"\nMasses\n")
        for (ty, sym, mass) in types.select(('type', 'symbol', 'mass')).rows():
            p(f" {ty:8} {mass:14.7f}  # {sym}")

        p(f"\nAtoms  # atomic\n")
        for (i, (ty, (x, y, z))) in enumerate(frame.select(('type', 'coords')).rows()):
            p(f" {i+1:8} {ty:4} {x:14.7f} {y:14.7f} {z:14.7f}")

        if (velocities := frame.velocities()) is not None:
            p(f"\nVelocities\n")
            for (i, (v_x, v_y, v_z)) in enumerate(velocities):
                p(f" {i+1:8} {v_x:14.7f} {v_y:14.7f} {v_z:14.7f}")