"""
pds_controls.py - Greffe des contrôles de formulaire (boutons) dans un .xlsx.

openpyxl ne sait pas écrire les contrôles de formulaire (boutons) : il les
supprime à l'enregistrement. Ce module ré-injecte, au niveau OOXML (zip/XML),
les boutons d'un onglet-boîte de référence du gabarit dans chaque onglet-boîte
du fichier généré, avec des identifiants (shapeId / spid / idmap) UNIQUES par
onglet pour éviter que Excel ne « répare » (et supprime) les contrôles.
"""

import re
import shutil
import zipfile
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger("crm_sig.pds")

CT_DRAWING = "application/vnd.openxmlformats-officedocument.drawing+xml"
CT_CTRLPROP = "application/vnd.ms-excel.controlproperties+xml"
CT_VML = "application/vnd.openxmlformats-officedocument.vmlDrawing"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"

# shapeId d'origine des 5 boutons dans le gabarit (idmap data=9 -> base 9216)
SHAPES_ORIG = [9217, 9218, 9219, 9220, 9221]
IDMAP_ORIG = 9

# Racine <worksheet> déclarant TOUS les espaces de noms nécessaires aux contrôles
# de formulaire (r, mc, x14, xdr, x14ac, xr*). Sans x14, Excel refuse le fichier
# (le bloc contrôles utilise mc:Choice Requires="x14").
_RACINE_WS = (
    '<worksheet '
    'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
    'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'mc:Ignorable="x14ac xr xr2 xr3" '
    'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" '
    'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
    'xmlns:xr2="http://schemas.microsoft.com/office/spreadsheetml/2015/revision2" '
    'xmlns:xr3="http://schemas.microsoft.com/office/spreadsheetml/2016/revision3">'
)


def _map_noms_sheets(zf):
    """Retourne {nom_onglet: 'sheetN.xml'} à partir de workbook.xml + rels (parsing XML robuste)."""
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {}
    for rel in rels:
        rid, tgt = rel.get("Id"), (rel.get("Target") or "")
        if "worksheets/" in tgt:
            relmap[rid] = tgt.split("worksheets/")[-1]  # tolère chemin absolu ou relatif
    out = {}
    for sh in wb.iter():
        if sh.tag.endswith("}sheet") or sh.tag == "sheet":
            nm, rid = sh.get("name"), sh.get("{%s}id" % ns_r)
            if nm and rid in relmap:
                out[nm] = relmap[rid]
    return out


def _substituer_ids(fragment, base, idmap):
    """Remplace les shapeId/spid d'origine par des identifiants uniques."""
    for k, old in enumerate(SHAPES_ORIG):
        new = base + 1 + k
        fragment = fragment.replace(f'"{old}"', f'"{new}"')
        fragment = fragment.replace(f'_x0000_s{old}', f'_x0000_s{new}')
    fragment = fragment.replace('data="%d"' % IDMAP_ORIG, 'data="%d"' % idmap)
    return fragment


def injecter_controles(chemin_xlsx, template_path, sheet_ref="xl/worksheets/sheet5.xml"):
    """
    Injecte les 5 boutons du gabarit dans chaque onglet-boîte de `chemin_xlsx`.
    Les onglets-boîtes sont ceux dont le nom ne fait pas partie des onglets
    techniques/gabarit (CABLE_PASSAGE, PDS_IMPORT, ORIGINE_EXTREMITE, ROP, PDS).
    """
    techniques = {"CABLE_PASSAGE", "PDS_IMPORT", "ORIGINE_EXTREMITE", "ROP", "PDS"}

    # 1. Lire les fragments du gabarit
    ztpl = zipfile.ZipFile(template_path)
    drawing_tpl = ztpl.read("xl/drawings/drawing1.xml").decode("utf-8", "ignore")
    vml_tpl = ztpl.read("xl/drawings/vmlDrawing1.vml").decode("utf-8", "ignore")
    ctrlprops = [ztpl.read(f"xl/ctrlProps/ctrlProp{i}.xml").decode("utf-8", "ignore")
                 for i in range(1, 6)]
    s_ref = ztpl.read(sheet_ref).decode("utf-8", "ignore")
    ztpl.close()
    debut = s_ref.find("<drawing r:id")
    bloc_tpl = s_ref[debut:s_ref.rfind("</worksheet>")] if debut != -1 else ""
    if not bloc_tpl:
        logger.warning("Bloc de contrôles introuvable dans le gabarit : boutons non greffés.")
        return

    # 2. Lire le fichier généré
    zin = zipfile.ZipFile(chemin_xlsx)
    contenu = {n: zin.read(n) for n in zin.namelist()}
    noms = _map_noms_sheets(zin)
    zin.close()

    boites = [(nm, fn) for nm, fn in noms.items() if nm not in techniques]
    if not boites:
        return

    nouvelles_parts = {}
    overrides = []
    for i, (nm, fn) in enumerate(sorted(boites, key=lambda x: x[1])):
        idmap = 100 + i
        base = idmap * 1024
        gid = f"g{i+1}"

        # Parts drawing + vml + 5 ctrlProps (avec IDs uniques)
        pdraw = f"xl/drawings/drawing_{gid}.xml"
        pvml = f"xl/drawings/vmlDrawing_{gid}.vml"
        pctrls = [f"xl/ctrlProps/ctrlProp_{gid}_{j+1}.xml" for j in range(5)]
        nouvelles_parts[pdraw] = _substituer_ids(drawing_tpl, base, idmap).encode("utf-8")
        nouvelles_parts[pvml] = _substituer_ids(vml_tpl, base, idmap).encode("utf-8")
        for j in range(5):
            nouvelles_parts[pctrls[j]] = ctrlprops[j].encode("utf-8")
        overrides.append(f'<Override PartName="/{pdraw}" ContentType="{CT_DRAWING}"/>')
        for p in pctrls:
            overrides.append(f'<Override PartName="/{p}" ContentType="{CT_CTRLPROP}"/>')

        # rIds uniques (n'entrent pas en collision avec openpyxl)
        rid_draw, rid_vml = "rId5001", "rId5002"
        rid_ctrls = [f"rId500{3+j}" for j in range(5)]  # rId5003..rId5007

        # Bloc de contrôles pour la feuille (remap des rId + IDs)
        bloc = _substituer_ids(bloc_tpl, base, idmap)
        # Déclarer le préfixe xdr (utilisé dans les ancres) sur le 1er mc:AlternateContent
        bloc = bloc.replace(
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">',
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
            ' xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">', 1)
        bloc = bloc.replace('r:id="rId2"', f'r:id="{rid_draw}"')
        bloc = bloc.replace('r:id="rId3"', f'r:id="{rid_vml}"')
        for j, old in enumerate(["rId4", "rId5", "rId6", "rId7", "rId8"]):
            bloc = bloc.replace(f'r:id="{old}"', f'r:id="{rid_ctrls[j]}"')

        # Injecter le bloc dans la feuille, avant </worksheet>
        sheet_xml = contenu[f"xl/worksheets/{fn}"].decode("utf-8", "ignore")
        # Racine complète (tous les espaces de noms) : indispensable pour Excel
        sheet_xml = re.sub(r"<worksheet\b[^>]*>", _RACINE_WS, sheet_xml, count=1)
        sheet_xml = sheet_xml.replace("</worksheet>", bloc + "</worksheet>")
        contenu[f"xl/worksheets/{fn}"] = sheet_xml.encode("utf-8")

        # rels de la feuille
        rels_name = f"xl/worksheets/_rels/{fn}.rels"
        liens = (
            f'<Relationship Id="{rid_draw}" Type="{REL}drawing" Target="../drawings/drawing_{gid}.xml"/>'
            f'<Relationship Id="{rid_vml}" Type="{REL}vmlDrawing" Target="../drawings/vmlDrawing_{gid}.vml"/>'
            + "".join(
                f'<Relationship Id="{rid_ctrls[j]}" Type="{REL}ctrlProp" Target="../ctrlProps/ctrlProp_{gid}_{j+1}.xml"/>'
                for j in range(5)
            )
        )
        if rels_name in contenu:
            r = contenu[rels_name].decode("utf-8", "ignore").replace("</Relationships>", liens + "</Relationships>")
        else:
            r = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 + liens + "</Relationships>")
        contenu[rels_name] = r.encode("utf-8")

    # 3. [Content_Types] : Default vml + Overrides drawing/ctrlProp
    ct = contenu["[Content_Types].xml"].decode("utf-8", "ignore")
    if "Extension=\"vml\"" not in ct:
        ct = ct.replace("</Types>", f'<Default Extension="vml" ContentType="{CT_VML}"/></Types>')
    ct = ct.replace("</Types>", "".join(overrides) + "</Types>")
    contenu["[Content_Types].xml"] = ct.encode("utf-8")
    contenu.update(nouvelles_parts)

    # 4. Réécrire le zip
    tmp = chemin_xlsx + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in contenu.items():
            zout.writestr(name, data)
    shutil.move(tmp, chemin_xlsx)
    logger.info(f"Boutons greffés dans {len(boites)} onglet(s) de {chemin_xlsx}")
