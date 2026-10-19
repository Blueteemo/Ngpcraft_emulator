"""SYNCTEST CONTINU — l'etape 4 de LINK_NETPLAY_STUDY, a la `ggpo_start_synctest`.

Pour CHAQUE trame : sauver, jouer la trame, garder le resultat, RESTAURER, rejouer la
MEME trame, comparer. Toute difference est un etat qui echappe a la serialisation -- et
c'est exactement ce qui fait desynchroniser un netplay, sauf qu'ici on le trouve en local,
sans deuxieme PC et sans testeur.

⚡ Nos desyncs connus avaient ete trouves au raisonnement et par un test a quatre
consoles. Ceci les sort tout seuls.
"""
import sys, ctypes
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import ngpc_native
from core import native
from core.native_session import NativeSession
import os
# Le corpus n'est pas dans le depot : chemin par variable d'environnement,
# avec un defaut local. Les ROMs manquantes sont SIGNALEES, pas ignorees en
# silence -- un balayage qui n'a rien teste ne doit pas dire « ok ».
ROMS = Path(os.environ.get("NGPCRAFT_ROMS", str(Path(__file__).resolve().parent.parent / "roms")))
MEM = ngpc_native.SHELL_MEM_LEN

def blob(m):
    return (bytes(memoryview(m.rtc()).cast("B"))
            + bytes(memoryview(m.cpu()).cast("B"))
            + bytes(memoryview(m.aux_state()).cast("B"))
            + bytes(memoryview(m.link_state()).cast("B"))
            + m.read(0, MEM))

def restore(m, b):
    rl = ctypes.sizeof(native.RtcState)
    m.set_rtc(native.RtcState.from_buffer_copy(b[:rl])); b = b[rl:]
    ct = type(m.cpu()); cl = ctypes.sizeof(ct)
    al = ctypes.sizeof(native.AuxState); ll = ctypes.sizeof(native.LinkState)
    m.write(0, b[cl + al + ll:])
    m.set_cpu(ct.from_buffer_copy(b[:cl]))
    m.set_aux_state(native.AuxState.from_buffer_copy(b[cl:cl + al]))
    m.set_link_state(native.LinkState.from_buffer_copy(b[cl + al:cl + al + ll]))

def ou(a, b):
    """Nommer la zone du premier octet different : un offset brut ne dit rien."""
    rl = ctypes.sizeof(native.RtcState)
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y: continue
        if i < rl: return f"RTC +{i}"
        i -= rl
        cl = ctypes.sizeof(native.CpuState)
        if i < cl: return f"CPU +{i}"
        i -= cl
        al = ctypes.sizeof(native.AuxState)
        if i < al: return f"AUX +{i}"
        i -= al
        ll = ctypes.sizeof(native.LinkState)
        if i < ll: return f"LINK +{i}"
        return f"MEMOIRE 0x{i - ll:06x}"
    return "?"

JEUX = ["Fatal Fury - First Contact (USA).ngc", "Metal Slug - 2nd Mission (USA).ngc",
        "Cool Boarders Pocket (Europe).ngc", "SNK Gals' Fighters (USA).ngc",
        "Sonic the Hedgehog Pocket Adventure (USA).ngc", "Puyo Pop (USA).ngc",
        "Big Bang Pro Wrestling (Japan) (En,Ja).ngc", "Pocket Tennis Color (USA).ngc",
        "Dark Arms - Beast Buster 1999 (USA).ngc", "Crush Roller (USA).ngc",
        "Magical Drop Pocket (USA).ngc", "Baseball Stars Color (USA).ngc"]
A, DOWN = 0x10, 0x02
N, DEPART = 150, 300
mauvais = 0
absents = [n for n in JEUX if not (ROMS / n).is_file()]
if absents:
    print(f"  ⚠️ {len(absents)} ROM(s) absentes, non testees : "
          + ", ".join(n[:28] for n in absents[:4]))
for nom in [n for n in JEUX if (ROMS / n).is_file()]:
    s = NativeSession(ROMS / nom, bios_path=REPO / "bios.bin", autosave=False)
    m = s.machine
    s.run_frames(DEPART)
    faute = None
    for f in range(N):
        pad = A if (f % 4) < 2 else (DOWN if (f % 20) < 2 else 0)
        m.write(0x00B0, bytes([pad]))
        avant = blob(m)
        s.run_frames(1); apres1 = blob(m)
        restore(m, avant)
        m.write(0x00B0, bytes([pad]))
        s.run_frames(1); apres2 = blob(m)
        if apres1 != apres2:
            faute = (f, sum(1 for x, y in zip(apres1, apres2) if x != y), ou(apres1, apres2))
            break
        restore(m, apres1)          # repartir de la premiere passe, pas de la seconde
    if faute:
        mauvais += 1
        print(f"  ⚠️ {nom[:34]:34s} trame {faute[0]:3d} : {faute[1]} octets, {faute[2]}", flush=True)
    else:
        print(f"  ok {nom[:34]:34s} {N} trames rejouees a l'identique", flush=True)
testes = len(JEUX) - len(absents)
print("")
print("  %d jeux testes, %d avec une divergence" % (testes, mauvais))
# ⚠️ Un balayage qui n'a RIEN teste sort en erreur : « 0 divergence sur 0 jeu »
# est le pire des rapports, il ressemble a un succes.
sys.exit(1 if (mauvais or not testes) else 0)
