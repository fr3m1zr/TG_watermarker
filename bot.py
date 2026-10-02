"""Telegram bot that adds EXIF details and a watermark to image files."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import logging
import os
import re
import unicodedata
import warnings
import zlib
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import exifread
import rawpy
from PIL import ExifTags, Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

register_heif_opener()

LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_IMAGE_PIXELS = 100_000_000
IMAGE_TOO_LARGE_MESSAGE = "圖片解析度/像素過高，請縮小圖片後重試。"
EXIF_IFD = 34665
RAW_FILE_EXTENSIONS = frozenset(
    {
        "3fr",
        "arw",
        "cr2",
        "cr3",
        "crw",
        "dcr",
        "dng",
        "erf",
        "iiq",
        "kdc",
        "mos",
        "mrw",
        "nef",
        "nrw",
        "orf",
        "pef",
        "raf",
        "raw",
        "rwl",
        "rw2",
        "srw",
        "x3f",
    }
)
RAW_BRIGHTNESS = 1.35
RAW_EXPOSURE_SHIFT = 1.35
EXIFREAD_TAGS = {
    "Image Make": "Make",
    "Image Model": "Model",
    "Image Orientation": "Orientation",
    "Image DateTime": "DateTime",
    "EXIF DateTimeOriginal": "DateTimeOriginal",
    "EXIF DateTimeDigitized": "DateTimeDigitized",
    "EXIF ExposureTime": "ExposureTime",
    "EXIF FNumber": "FNumber",
    "EXIF ISOSpeedRatings": "ISOSpeedRatings",
    "EXIF PhotographicSensitivity": "PhotographicSensitivity",
    "EXIF FocalLength": "FocalLength",
    "EXIF FocalLengthIn35mmFilm": "FocalLengthIn35mmFilm",
    "EXIF LensModel": "LensModel",
}
PANEL_BACKGROUND = (247, 245, 240)
PANEL_INK = (28, 29, 31)
PANEL_MUTED = (119, 116, 110)
PANEL_ACCENT = (190, 105, 67)
PANEL_HEIGHT_RATIO = 0.078
PANEL_MIN_HEIGHT = 78
BRAND_ICON_DIR = Path(__file__).resolve().parent / "assets" / "brands"
LENS_BADGE_DIR = Path(__file__).resolve().parent / "assets" / "lenses"
LENS_BADGE_HEIGHT_FACTORS = {
    "sigma": 0.82,
}
BRAND_NAMES = {
    "sony": "SONY",
    "sigma": "SIGMA",
    "nikon": "NIKON",
    "canon": "Canon",
    "leica": "LEICA",
    "apple": "APPLE",
    "ricoh": "RICOH",
    "fujifilm": "FUJIFILM",
    "panasonic": "LUMIX",
    "olympus": "OLYMPUS",
    "om digital": "OM SYSTEM",
    "pentax": "PENTAX",
    "hasselblad": "HASSELBLAD",
}


# Generated from camera_resolutions.csv. Dockerfile currently copies only bot.py,
# so the catalogue is embedded here for the deployed bot and retained as a
# readable source file for review. It contains maximum still-image dimensions
# or explicitly marked estimates, not an observed original frame size.
# A derived crop is an estimate.
# Primary dataset: CameraDatabase by leavestylecode, copyright (c) 2025,
# https://github.com/leavestylecode/CameraDatabase (MIT License).
# MIT notice: Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files, to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, subject to inclusion of this notice. THE SOFTWARE IS
# PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO MERCHANTABILITY, FITNESS FOR A PARTICULAR
# PURPOSE AND NONINFRINGEMENT. The authors are not liable for any claim,
# damages or other liability arising from use of the Software.
# Apple iPhone 15 Pro Max source: CrateCamDB by CrateTools, CC BY 4.0,
# https://github.com/CrateTools/CrateCamDB . Other supplemental sources are
# manufacturer pixel dimensions and Apple Developer still-image sizes; full
# record URLs are in camera_resolutions.csv.
# iPhone 18 Pro/Pro Max 8064 x 6048 is a user-authorized ESTIMATE from an
# iPhone 17 Pro proxy, not a verified iPhone 18 raster; provenance is in CSV.
# The compressed payload retains the original 2249 rows; the two estimated
# additions below are readable. The SHA256 covers their combined CSV body.
_CAMERA_CATALOG_SHA256 = (
    "dc7c83f79add0f8a2ca2fe0f7d1e4da0a4dfbecbed9fdc99cae974194107dcf0"
)
_CAMERA_CATALOG_B85 = (
    "c-ozPTX!3`(&hX73O>%6MP+aYdft3QUF?rc7&RmH@Fh#Jm6+$qmX~DbIN!>T|A1~@np8J>?|$)GruHrr3WY-9(!g)e_n$vZKRtbT"
    "c%1%c^Wp2q>Boo7A0NL??;kh!FAp!%m#1&f?;rlx{g*F~57W)^<J0GdiL{gD^V4K;|9u*SGgBu_m`>k6-+z7h_5J;)55FD5-BhcH"
    "QYNA5zx?{);m?Q1r!NoBzurH;|G4?{qyRTrK7M=QX)jYFw3=$63`}W?VtJltj4LScjBy2(7c`|p81L^tKYgCgHh*ls-ak&>R=<=}"
    ">4ca{75`a2{paDi{P^^BQc58vv$t1oS8wK%i}~{U@3+<e-~BL+UiI*N|LceQulK)2x9ku)N&L5E)F_J8;#l^~hxEs`$cd6Q9+50F"
    "k_-1rvV|q9oX*8-oePah*2%(MX4V-q*DOT1?h~%_!tW93Mog9RYAU@jM+S)kj_oWkaXs=(&JqT*gwcJ%<*}{?Q#PWMEuy%>9M|2P"
    "*4<*_dgR$xri9dUQCyI2s-#p$>bVnn9usH##OY&k0TNgDm)4-smMEL>a2x^9&J>z%+O4a9EqIGZB->hL7qwMhyJNxrwZ;~f+t@-x"
    "*HrqXABEQ%7r4eQW99H#<pK_d4Y|h{p(7Z=?N?+3`{K1=3(FOg)uu;qandEE!1`E`aS5qjH=TqHT-"
    "aRG+1GB2OLn<&%y#rV*x!n*SG;b#fO|y~FN}%3TI-WuUA&g838jmiJzjT`0FUdNp0V*{xPdaJ&1C)b^r>gSIf=k?4N3GpA;+9ZGl"
    ";aeZ>K8FETlc{1}9xh3ryTgpFDXg4@y-2>`7#0Ca+Lf5cpGd%T^*U8`E2N+d*ZsH`S;3#ZPteEoM_4LTKD6{kALf=Ha)8$4OQGoq"
    "qhU&CAQ=GAn7e^`T}`*$>pINGpSxYGYmfTT|4^9g3<#PHmM6v(Z<Tj;6>1??I%EGF|HPb?-lDRnB!wEtylRuGzr|uZ($+v;8?M3#"
    "_6}ir1E`dv8Yzu3nWp`7tBVh~oyzLkGNPwRv55m6g~Qm;<J2n-o)3=(2Tm+*Z+*%80V57RK8+BbF|-9a6SM=&Xpggd8y9bcCovsy"
    "uG8xPSh4ilx@2fzHP`58)%h$9!LhI3Gg@w^Qe1jEH%~0KS|C7aPX<VnFj~J`CzmKXa)YW5_7cM5n0pjx26(U#Fs_Vlt7fz89ci?@"
    "z->QTL||12lqfxdfs~I0|SvU|;B4h0Rgu+hl|kNk+^kr^e+SZ<eOs$=cEk0FxIZBgz7^-y+6t6euvuEfkE@AkLyVanfXUBQ5fj+T"
    "A-?x#+lXphoV{qYyv1!Q#%1#EQR>0tTW;u+47lG{_j~Y^-t~H#vuqJ6nQyv`WzjZb7KS!pu#p-7&d`kqUDZ58?u-z&5RuCrN1Zi&"
    "-n&c|e3Z=}qcbL@_90r8Xy8SbHSG42#ewX^=drh!ud0Q#IU)jA0Sxm5Ae-xXrK(d)g!?2K7Ky?5Q69L<XQo)b+Sw{c&9aBID&$hT"
    "F;r0|~)afPd{=$KC}P!=F|poXE&lWC`U)XL2@GDL~15q=>i}|Bw+lA$KtanFF=(ab`m@-a_+@1L1aRLb2T%&EqS$vK5ut9Wh`^+F"
    "48sh-n2X58Nwbif9w9RR9UVTxGlEXM5Jz(&E5wkuCcBWmZbs8o2IWtxL-w^?u%UBg@J%D=OxsCasj{<nKocnS(-RNFkTvSDFNdXz"
    "0X<9p~>8r6Q<*$*|d6lzqxX&)zE|MP7)`+eRO3<NB+0AnBB0q*&0OtsTH=1uW<^IEG>H8=02NqS(R-2+hLV>(Ko@vCVn4B@J-YF3"
    "O^Wv}bFQg>vzsO)w-uwKsbaIE|+*t43H>)t24&2Y|p@R++6<49BvCIFg_#KTmZ&EDl5Xll>WhBgryGw6@+Itsoj)Yrg&Th&JDbUy"
    "5V6PG!huG-8BGWHTDoJ}Uv>HHkkuJOSVxiT9$ly9MB5r(?Q>DOkLv@OCQGK`rl)!PGW;gA9X;GnRB7OFEC$T;p2PKH37{E$0l@k$"
    "8V{fCHc;e><F*9L5(X#{q^Zj*CK0a*f~=XC$Y&8mGjQ=`qgI<BsRt+6&I@yrkX2E4rDzr797)t{DW^3PExSPq36aF^UpCI+Ij-K2"
    "scAQ}7GPWX;XA@pP`codVn4EH0+jm!K%wh}t(zBT5}aDO{92&OWxYX;sT9U@=N`im}_7E-XiP%Gn-"
    "bk+_AleJy0GOz&}uQyHdH7rWI(qhgv+)?QYq#cZ`Ps2C?+$#7d$EGolys^Yg|JSrx<R>g0t6HpmNrz&BqN&$+|ot-MW|E?z(*k!c"
    "p>Qr(5vKIw+Dy=)Uvitt&aIH+IR_-7Q^}{&#Ns^ic&9q+HNb(0ss4HsMDJdKzSxVC6O}DK0J0!(+IzelFO!uxGo=R$b;4nt+&T{W"
    "14a<4_@^NByr<{XEa!}5dWHNy(nLY{8sWW9(wU6<KMdB1&bc%}<57;ei6GiL1CjggBrg9|}2e-^%e-+hc;EKtDuJjI#tdv~ov?np"
    "NUC_c)Db|%`r8D~;EuLfY=qpdPttac?N7lhpJ2)+3K{-=FR;L(OogW#cew5r&`Hza3Ml7G7Br|D>_?WrWak5@CK?qIuAw$$h>H-K"
    "zrL4b0!6R}hsTq4onlznt=J4Ens8n@*@z_x+YW|(hQ#0MV)W_ce0R&E;!?$lp94?Ke;FeqnD7h8Yl3P$Mx%JeNTiz<U)zp$(Os&5"
    "`Xsf?FCSRvm{`AWqK%Q#b?nv6k9)CNbAZcyCI}&&DVTVDKGp}}eb|0DUQ&LePa$-h$w|Rbee0X{3tM1*~n~V8HdHjLQ?nk5y+iOX"
    "CY)L0Daa~of_k(wb0-(CvoWF<@#SqPom!OXY+SAW55rIA@@Z@trKwM!?R!@&Vs#dSR3d?=E)skFEa>p?;#0UzzZW=k;a_X-rEHt8"
    "5+eIr*H_a|gM~-DTL?g$syMmG9d5+?%qr?b~<V~Q|$+RBf=n)(<g5yST{0L6qIhr^12n+DL?~x-<7X1*8;yISDjvY}QD@T-by2^-"
    "f^azeQS)&6ujxWZE5ga)}HN|uM7z$q<KcYH*Om*}K4($c2N-|S}TRHr#S{p5LYZAYI``708&Eu!(KR18;>*=@2_3TXF<>q{^333P"
    "TTFl(Tcj?g2GB-s(z{|53@2_XgDU0qkhBjF54me7Ym3z7Q{IJ}7ufM0woZxtkj}hm;8G_RJlYy@ZDY`euP#BgQ$FihyVN5YvY&d+"
    "#ALM#PFtLDSx2X(J0mkpDL9xOpRxyTU$r`zXZt3iB?1TYf6=PWSDdJ4#-bIj1pGoI|WBbQoB5gMI90-=x$gza9ZsY1t01Rd%Z%IO"
    "aspmC8k&z`5Buh{%L9+zI5-cBp($VRypA%6f9iGl*m|X-*5;RHBLUVP{`<erT^MR$w4yaA`I8+y5+m3azBRv**(nk8p^(_EvKccE"
    "+KC~<=#*}6Ar~h6P+#v*~a3ry2tq&P81TV7d&aVmefi+b`pGjFGLQpipk_1ae$RgFjr0>(xmsJQsk%ZfUy`xTsWf_t#L(>dHGh%B"
    "P6>X&zYOBC92EJbDWLTDA_a`l3hUFNQV!fnNte2$qH<n@Se^?6J=xG^^WYk|EkqpZ+*hY^Gw$UwJ4@+z1NXCV2jfL>Idtv2>vGBs"
    "~Mr)HYmRKI7;eJlS1mcV8OmW%pr5rKtMw0mb1J!3Pr5sU?5~E1sqPpR<(_;$KqYAP-M@C~)ni(N^_l_*Z5~5;E?s93y<l&b_jba%"
    "w#t36_>&UXBShW3|AdB$FrjuAPJ}E{7o=X#U;flyiFH;z<yF>qScNd9`Jt+dy#W*pFB>9!3$g(3!Vy)on{23pDE}b3YJI)EZGR>z"
    "@gvESahEC3@Jf@eIY#FL4179Ay^matuEngMS7Zb!74n0-{IU?D)3?u9o_<^+GZE&4VQ8GRd36dlzKF~yx8bh)pNP3KVdQ3?p!t~o"
    "R9j1*FjCATIK%>sv@=Q{L+dIiExV&R`2Hr_2{zEbpsghz<vSb04tpcl&k}N@yc+28hg&egP@=kdYA4q))4o@zvyo-Y^hqol&llZ{"
    "l$%WQ;^fKr>dKvT`y$t$}UZQ;0EI5)3%QDC{n|Jhr<2!o6@g2S3_^witE`x0kdiC%fy?XeLUOjwAuO7amR}Uk@)AjIdJzAGyfm#{"
    "C)+1ej!!az~g6W<VlA&qFzRh7qU~BH#_E<~SR%<25daPUV%3=)7MxV82qt9Bi(PwSR&7{PzEJLzoc(Mf-E*$|Nrt`}iC7WYJ;L7;"
    "T!~Nq#&n9==bZ$@66s?q$bCt55_SEz1`h)^P5%sD)r&Xl9G-oSg;|Pdi$=Od??^iHRTuRR~G|liVBhXr<y?l}pSVqBWrRXVjTEa*"
    "irbDLhFU3dFA4lx%M2_V-w$_d0Sf1lXloJ{b5iLtqjNy=ZYGeeOQIHJHJ7;}Q<(fJ$R;Dc@j=;{DB7?QB)^r(~F2m4e7`lwZ_B(M"
    "{uB>|rRZz^+Sq9yVhHex?o7h;IF>qCE2gB2hz%ne~d94s5Hh)ViN4c0)V~#tPcAP_7H?Fk(xqap+V`-bSjQDz1>6(m@IeHK|ir)c"
    "EtTmrOI6Fc=x??!HV>r5F_`R1i`(|a>ru0ms2FXtYf?_KaG|ezH!_o}8@e48L99CIK^2}{n&?YXT!R|;(GdHUboH!_G6ppS4?ffw"
    "ZWGgbSKWyVD^)n>Q7&=#uX6zDRWv~!7XS0Wu;tAY>=!#ZJ(<P8;*@|7FU0IqyE@t13ISX3t7b?N@r&^Nhdx^lUy+wv(i%?wOiwsR"
    "O49&1~#XZYVJb|r-Qe+XD)k$lTKpWPvmG&$laD=s_`f1uwppOczC7p~#N@5UM{92Mr>ubf;T+!KKq_7Ol>7)fIUa$m7;`QB<WLOg"
    "KIlLw3L2GhxJ8jUgtEiFVc@8%itpnK|!iahV5qOT|^eP04=L1wB3ZBE(13${SsLtxWIN+Q31T{F+9`3)$UX_us9>*%byNY074>4i"
    ")AMk(#Lg&LR7Z@I`Vm0}A|M+_ocudGn5UaB{GWUq#5m2B=g#xp$>b8Lv1$q#8fmZuM!N3wlqSd}IxmP#aKs)v6!<hPVs~qrJf<<2"
    "|RAQ_)rsM)Od3yL7l2~8xi^+^o1y(&mHK;&|jdBSnP>~8nuF$%#;GVl0co4e{RWJ}2iI<MH&`Jv&SwCD&fMGP+k5_0vUd6c_gap?"
    "es`_OW$o3Hl2D*q`!N^<8L<#v8t2%h!Nd=xLNiR-AmAM3i!leYgeOX|$<mzXz<e#$#x<B5(ygWSq_IUpxntPTWQnuoJ8q$uKtJ9w"
    "Y;Mec+!>^mo5k<|?&AR7PDP7byc%zOP>cc5KmN39bWjsD?-rrBpC;xl(*B2?(KXO&rV$bHkR1=x|?yU_}xfy&Lm^~7<1<qsB)~`L"
    "tUSYE&EO$C#5Bv7Lu649h5v!dlqD9vod@Z@DHuZ6@&i>Q}rZ?kNdL`StJkShRy8&7V7%Syapr4wA(blRwFaW^KADhQ--*cJt5imK"
    "Bs^9JmVhEZRY0?9Gg&KrLCrVR%r3W>gm>+cR`m|&4(1fBvFB*qZF7_i^#Fp1Zq58oWA2we%PoL9u+qOverxyLrvY0HMK0m$x`26%"
    "~TZnN`Aq^HO;p}23Ss;=lyhg`Hu_|?*&abq0;Jx+;cnN4LMWd~b(d{2QybBrp+0E3ad7D-J?Y)q48byVv>wJ`b3yNb#k-RKD?$nF"
    "<K>?@&Fh?<2ZNA=r-h4xBS0v-9bP=7Ey~g`_8~9|jTxP7?g4HzWXjp4QujBu$lhxwDtNUY4Sr_^+5yJmt>eGbYW=bu0-YA4a;0nO"
    "Z8;NlU0jgcjfqF;KS&7I(>@(MhtW{G9pDJO}60ugk*Hc$>1fzyW)!Wu^Da}7}*Tn7#b(SzV6y0ve)yz8D5l{?gD7U*27z7Ha@HAv"
    "VA`=i0S?$cJQ$wb&6FW2D9r*VCdGiDS&n^(qFI3?%0Zw_U3bwZyE0vS;dGqh5&r>;hbGJLmFS=sb4)!kkP4KGcrGPz3K`0HCn1%("
    "dn!_CZC>x_e3<<jPDuNxVgtU(l%OpTvuUkmgtwQQ}tB31&{`B<tCEa11VyR4vbYcjaPO8T=E((@PnL)J7-$;6bRutMY=(G=(-c$s"
    "tsAc}L;S;o?(3V1b25oA+(}@b1@z_1O*1P|R<_TI;XiK48OYh-Y1_oVc2$}iB3A(mP|90#N+E8dmp*^K{!N{(=Oz(sC$5_wwG4Jw"
    "Mik_goB}5mD=)h=QAGXY#R5(F<3LS|48v-ZV;-ZV}e&H+Z3l5fqU`w=0`WObUUP0L6&2Z|nhvuJLe+3q~mr-7$8R11Oy!YjZ6ZD;"
    "=gx^WY!4(JB1pJ?tQT?BeLVL!F^X#hUDV7M=^MOloUZE*PIB<4?b&6Ja2%gLQ`M^`OVMJRzOwOfaBnL)vL4Xz0;z}}LMKqz(jATQ"
    "!VE)FSQ?SE3?Ob^V9Pk)CR|O$i6VZLHB_mlfT_sMv2tAlcJ=atnEzz+Y5tJM=7|os9Z7DiGEIUxL17*FSDYPNbONob?B{jM%xjbY"
    "ksmx-j@M+0X6LlQ8PvjKMwE9wOO15FphNz<z6qr$?o)+UiVk!Gs$?3v$;H70cSvq`=n~+K+m(9|BS_9M40m3O0daivu*X6yZ8psC"
    "<9vD>$Msh(lP+<vlDTtJ-6skF6qvde2UrIa#mi4{7;5b=Qk+{C{=F7iuYo|BuYXX{>q(|*J0x>u=FVv&yMyVvx@k_-d=cQsY-"
    "BM{vHWkL}D^nPId&*;!r+QGjiv`af>uXpDxF$LVWpXVjlWQhUXi2q?X2ObARAZ(O-Ei5OlWn-Bv>e=Va8JPv;a7%p6~nc@8Te@P6"
    "wbB3;hNNNO=<}%S|*TN!J*$XXkt>8E>YpojHg)no)NB^Hq7l3x4@L%oKso#o>7H!L2H>XZXJd8O!E+n7bT^J6C|1m5KdCUxok4R-"
    "&4}5RKh8$c{rzani9@AtuvHx!wI(>+;MPj;2gTx=M+v&-b$)}Qq$S`ej!GadU4h&Xv47d%m5S^$${(r5&oX2W#9&^AUWw=4=spzT"
    "L{Z2UU2m+NX5BeQPkZZUHWQnEF$1s_b<4~VZrt1pL34~A7h)nYjm!1SC1?(8fu`KoHobYbicMU+4xXspuMPTL{!yCl9@vkvw8t!="
    "y2dnT{qWR)hG1&K`#iZ%3aU11ATu1P<nUIVWv2pzYgetLd>Ak85Tg=A?eASrJU*Jo67pobf&HAS##z@_YP(-n8jc(h;6GVDSgN;G"
    "9a@%7t?=2a%K>pH{v_cMCJu34>*B4vxrXhk^>+La>8bIH*|<-W);+e7IPbm`9pooa2VzQYdEB*xkK0JKaDwa5<z*SC7avvhdjPX@"
    "&=p8$1njDDIuGBti98!DqsuDV|@_2uHAncdlrzEO!<7A%O$hPEb0P*Nk~{UMM!NKox5NA0_l^#1JPtI!<iM$Cq7xL&XqZ9?2jOW9"
    "R^;g&$|x_`(5Kg{rG~QA}CGMLjRC;p#eD>C~Q6X{r#{Trdz3oL3Y^f*S5YsU}&sbTd2JHE`nj48}31C*V1bMhUo8y-G1#;k;AY6U"
    "<v^9JD~PW4)gT**QYPvUZ!t0&yzPId;Vz;U7yL^!QD1)a{2K5eE+;jU!WL}w7Y(}|C@*%_&h4oe~!6@YJD3#1kIkgKEjE4=0JW6n"
    "Sao{1vItD{tuXsp%sd?X!%YcEp(o4oB~@XWIu2?02QX;{%&&=&A}B1Hxyi=xTy3mYY@z7obGs^H;pEB-f8vTbVVE^W#`vuN9f&Mv"
    "WWlay`@38=HP~cOAbB!OnM7#DcO!fdj@UxkDI4&j!fQB%%M4vUTy>@-BQB0Mwxtgc$}0^kGl`l23UUhHG>v9FdFUE@BeV14Hg{SG"
    "N!a5TIX)_;bHLQ`WA<A;QCpJ^2^inSAyh*A6-9y{qRE&Z7?1i4-XQ6qWzI6A&zR)K|-Y0H5Tfu-qcvg1>%eo$2taUajb9ZhV-5w0"
    "`0OvfvHw7y98<=dkYQ(7DpFV4uKT|b_}?{2l`pc_||~fS;~;+{ohv~a2m^XBM@tV8hC>N9V*ca3>O4uMP0x^i}dT+eh>x*r38lkB"
    "Z8Ghx?copiv;o6VU&i7cMO>FWsAC=m6TXTp|yo`t%xS_&xE#c@)6OFl1(Hw5nWg~RhgwLe4z<wL@F4m@nq>DEkZyrk-"
    "NpLjT4ibvY8p>6}^ao6DvFW-CheOn$;OSP;8o&{ol#O?N9U7r2Zc=eqNlJ{DGwwP`2e8iP97bv>?!8_OM6xu&6!kQ8g`EO^a5uc?"
    "}lOzd12A2)wMF6B#xVQbN92BQ1(l0?FnRQbQ{~329)N7Os(lWqKGHU}S+J738Qj5Edx~GHFRj1tT?#G%(U4eW(V)Msm%qf~xZ>!Z"
    "y&uS_ZiA0+l)N@#8U4fL78XwL4UMEzvrqk0U`O+(_ENNRJ{LYeJPbxSfnp*|)uppc;o79BN@*(cWY{T6h4R)Z$KRapT)$d=v^BKS"
    "C`GHMjy6QsC{)#0qG~<n2uzC4j;1)Bk&Z`t4z|s6}spdDr4!-zHMjheK3qvLX|#K1FPrex{3czurIY_VADdBo$-9Zkeziv|&;yM3"
    "sJfynp|1Bs?U-r_JZht`dmyr<_TNG{sMgvsp|d7tr469z0pxKmWVl>F&SSrkBLA1f^KL{mv3tMpKVlsu~r8wv0k{-xy;^P-84Fev"
    "G80MAH2&RuNcDU?J0Nv7V^goT=O#^U6K?g^|rzZqAr)&e(457_eu+fdLl;c)_&cLV=RcE)h2>uhS+uATa^$;s%6TG1**~5O_J!*e"
    "og`6^z7k<cPG*_7*VVOJLb-=0G}F%Yb*=M3BzS-VcxnCUDV!hjW7nc7gzVY@!^Ip%Dw@90@FO@Y%q(crB!L7PZ-#lLFSjH5-zc5?"
    "-N#zy9_3?Iqn44m8CU3r-I9aLK_H2iIa!-cbz06=2x{6t)<!gTD~A*8)1mwM83jVR46+4BBFInLSzx!X0Bs%Rgayf_GqqXwY6@;e"
    "C%5L<@OvKD>u(#xNdSY1qM4<GXlel_d$9vZbNWmO?uU?ODqNN_;_z57_?34t-ee3jkVCXkCBL8vWM*Juesvy^W<Xq2M}S!G)QaL1"
    "Ibi9<DgJX5h<o7KR$NS5M#m_&9Ogc)TM~VQNQGf>tkZO}HaTzNFRFS@41|c}?4{$ioWWK0N>VKn#>Meu6DGZ7J3ItZtp)hE%G7h@"
    "B!(qCkZL4GM&&)(Pl9pb{WZ3ve<Sp>lD9PA6AqSW>&HCk}vft?Ip91b9_%_5g-YNQELDEHj<sNLIph4Y1r8{1~|!p86*=zEH3aB@"
    ")(V$51pwQyLixZIL<19_=_ZwvlrP%MR+Vw^wK{NJz}L6H>!SV^O4q^>lFQ4ldopG6QV+0z+Qc#}Sayuj^Arm`n?{dqP@dXiP}ZLv"
    "GNBnvl3m4I>R^rJH(79z;4AiKR@{S^kdy11`NH15A7(Fo3{9KwIHeI;4%nv6Npy0@pxH|1|lq-G%&VF)9Cg`TFo_^3&q1YZLJ3=e"
    "(eO==9kNwK=g)xs~`MsK%j|fDY_=e!2gA|MK*C^L{!zd-;6-Wm4Xry-7FhW)A9hb9C<VnjVV4IP-6xHs2?+^Rs_mT_-O|N5s4n7^"
    "Rfgx%&n>D3|NBo?08O|4GMAUT9xE)c#$e{8^uX+28H|+8HA7N*=xB()v9%Fv2e|*J%-ItNGX~P3N<oogCY#V<G--{YPS=Jge}v<r"
    "#37YLiORZ?CQ$YshQQGOBd9MDyfg8bR8B5N-2c7MM8JU*?r;I$Gu}?G9Qg$sWzefKt|16tI*FnsbwTQMa3sZak32#OS0zj%C%_-Y"
    "`LPDw4D+eOKqdTzQDS?v5SnbZH>QFS_{!<nZhI!u7)-(Gr~g07>wj5jtuH@E7N2dM0z(NEhWYdz(Zj!{+W(>|}T>foL(V+ZAd3UP"
    "^%q$Y#ymoTK-*+NEl=RNW&$N>G5IjeG5QN}z4C<UMPtXvLN~_W?sUg?GoL=c^y+>X%ZD;Mfry*X?xI3Kh_HhqeF<3dlZk*F(uJ&-"
    "QRskDeYCFv7Yj(Ee3spJnQy=)OioTT$B{>nOAtweORF8aV%~dk;tPo?tB9gG<X7<M?)TBWmMm%Bp*KT$SK3vpU+JX$w%`NVn2G8f"
    "cW?rvW<Fu1t>x2IE)xH$r<jo(^>tpJ7$`M~(V8f{t#LR3qw>rims!5~^|Swna#<(5_Tz-Uj)@E<GGRT34D6CY9#9d3AI({?Q)-D8"
    "ce}w<C;b1wWow*7BCOw$CIsfPtfOwnv16C4}zLx^fPb;B&_fdMJV)T`O;UH1%j*z3;J>M}08xbqyn8S;5DPf-hz*>B;Omg>$kOuH"
    "}f<u9vE*b?Sy?N2q7Vm@d#Aeyw<O=P!up<BX_J{r23e6`!5F{hDe0!?i?(20D_iHMMopPz2u=dW?~D=#C$24bDr}hIdcXvy!tmaG"
    "^b6Z;cMUYs-6~<ww!A>rSNOAHbR8+S7q^?fKQUbzswqD|*f8O41H`MX%Ca>DDd@#-OLGSB_?QTB~4RZ}-%{w<Fh8kVpT+L|^hvcg"
    "Z)zrRFJ?r#RZOmwaPf2AXos8~Iw%es!&RilHg#<43ak3rkZfzLbh@%9!vnJD&}ur!Q8=ow4%0=To}m&gE3gIFu-D!O%XJ^bj;Zm+"
    "VM4KUady>*nWLvnwg{bCbP*tcRo7gkyeg`C_c#tK$W)TA<V6IUl~~>CtXdv-&k_PO)^bh^5dQMDx;(pwLTp^D=)BubUI-yghR0g_"
    "e1YlsbJoq@)utrF$|I|6u=a|F?kUrFY8eCCUKnoj#6GX~y8uPE~0-;#HQX(93`GHQ%MyBf|HZ?}lqd=l}CH-*wk|L@_4&Bxg6r(t"
    "UUB=&rl=JcV8`De1^v(%bZ<rWtF=He|WxPjHlL%~LF28P8J!uUNrSR;BD&_E>?(W>!beaI|5F@_TlnSKu!6s_YYh@?8%fTbW<2>1"
    "1}50w@<gB8BdCk<-0zu}-H(FbSHCK&v_gcX-4a-6@U|Bwtm-#=TYS$C-E4dnjTAr~8n^x-@7bxYh`L)QTYA_wY5!7d>udu@<dLI+"
    "$1dkg>k$KI5>iB*^bRQM0ZvJFWYIX*$jcOX$vsSM}}=CZi{yuF&Q4bvjV->NA3-lc753J{>HogXz=3p*nQOzSTN_eAlNzKsE3^8c"
    "0+?=<z2xT!*s2&QdRLC$sZf!`#xs!{@K}->3g)^YV2v+dt+R27q%MsbFZCdrR~fi3oMZ&Za(%9M%|-gRPtQ{haKK{0;2l?ZAt42V"
    "7dk%B^R$fe7-y&eC&${g1KkVKVn-=mE7~HAK)l_j2<A)qE9z0QZ+Q*5)=+g&bT!6}K3w{+<K@f~5)!my(1O_oc|9vX9^HVV`nO(e"
    "6>txhG%us5o}#&Ijya3W9y#mT7V=8v76uWoq1{{PyMH8AHzyvq*^*t^_7erH}HOrd63XyF?mx33Q)Db(WveH?W_+eBC@v(w*_!*d"
    "m9p8W|$CP!4-lGUyuyJ!i0&1o-X-1{<B<QaHh;`ZbGqnV7pnCpG$Ssy+r=0b}!TIGjM|IM!z0n>fK*7+Wu;rx}hMYz&`X+tMP<;Z"
    "!jMev`k1{}?;)fx;116IdhQ?tC$D4EPAE7;Jvu;Rri$r1l7FaQj|Z0_$-5UU&*SC%_iR211Eoafc{yxz`2mXm8IX4xTIABj!Xa&&"
    "&GsV&+PYQ@Ety#vv(VgI*9lZ$a2&LG-2t(VG^8ofkxJS`f~;ARKN{!NcvM${+u0W0xLRVHt4j4d<E>ZW;JmQsDenLK}>yR;7eBC?"
    ")PcL*GCiVTmlW#C;+yJ>ghOU^h6HNWe;K3G9ZzekQR0jbj}gDTmHT9AP~iD9c-1*<0LTD`E^-5o5rL7z1vJjP+I#*m7V4zYV@43~"
    "@&o;*K!H9WiL#5t03l7_{!L&hRLiz>}+GNf88tRuq`kMdR+VguB$;a)WzPNhHw~til@VQ9lDt@$=AGi!BUFt$+3aC^k#}Wa%DOAi"
    "cXfvj_^xsVb-+Rag1@oS!%EpFU2{@4q~z1r8yu>c`4+S-}w_CS6y##(f2D#1oA20!CkHSnn(Sf@7=PGRg^5=D*53K@WTab%GACC2"
    "q3E*iN9?vHTdT2xy68HEiG;EpV|W7x_-00>@@oa*m-6hkCqm1MFcp$r1Xm<vzt`x5-Yy={UJht%jgm%j5t$KP@>0Q)tUL(2djCb<"
    "tC_BfRKl;3<bwv?tINZjdz+DbmU0{zE%EkU~Hj@ZHKFAd7(9FzCu5A^XZBp*o@BfHjN@`L~m{sN(4Q$P8-WM#L@fCbt;3!|WfB91"
    "KSX>;!1l7TQqgSTzKm-*j%lviIt>ao;iEZElNw3#)D>Z={Op%%@M2OZ7u~OY!~lQ*QUTO>`4w&Vo%`{ewNTKUkoukr}m{KYV_vi&"
    "kM<G*;;1>D%Y8iHO0MQp6I{vk?a+-Q@NhXt~HP6Wg45wN>%V{9qvR^*aBILW5dX!<ZWh#DE)%fPm5YW_F%VrCbdUpb2So_C(A9SU"
    "*GyiL@F;y5?Y0g4_N<Eg}8z_>^E9D}eF3MtTI9X4=_F{2r!(kb5`qq`)3!o7p{r#ENyp0X(8SCTx*C|NFA}<J0~0pO?F{5YqHve*"
    "cn-fji7(J}uJ4@=kY!zy?*$<Ye*i{JeR5oSKx3)*neu>p%Yb@<HxlMNC*-=<&9fr_X;)@8vYO^vOx*RlZwD8V?3hF3r|*6>LxrLM"
    "3m_j0836jZfo+Psd{F6D>Wm{P}Qs{=7b0HZ1BUHcqs+>%5eB3#x}P1cso>=7D5Y&#82;X2sB0+*s`K%g6hPM{yHe<W25i-Vv}ibo"
    "B74?Tl@0Y&lr(9dH8JsKMUr=874clG!1wWw40*Z?QED#cBd;2&{#%siVQJQ2RgJ#`gs(w!ppdO;O-pHY?{hXK(72rvB4&ukeb+YZ"
    "71Vt}++a0eDU7qe*?VCG`m;o~*AS^=bCAQ++#=@7H)%z9aEr8!Wr09k1}7)IVK??^3o`zsNm|F9eGJ@uE7@U|nnMk6=X;*b;&*CH"
    "RH`uL!Usu;|E}u;-c(e2Id~`6_jO(98|mQnEdRwv=qk$qwMqU89`^+onMW2JJnE#z(rEPz;Zu=YRe7@O<|2@hN?Xa(*^T;Y4a<rc"
    "x+_ay-qsw8G|As{1&9<_;)#lSY&?q8Kl-_g;2$<Oq(>?KE^!g39h_b#p>)uc(t!@v2>A?<wwNWS2m@7}t`m$#s4sy@!%rG3ewdTC"
    "x1e?H)>YZM&1B>FR5dy_%wvV@7bYdxG7J?1#|Zl<d6;ofJ=7%=7+I(1jFyA^AP19*PjRBMM5S{dcP%Nr`c+5hOjL=S0$&lI*S|!h"
    "B|odX|nKl^s!x8^Q4-T<GW>I*?P0X`#Z7;1nY`v=QD(x{uGZd(mBt1wDo?=)vtr$%eNd73<B*?1Kf}48c}pnQ{S|qef6h6q7#;(a"
    "BM4{=8K6XPRzWpjq9rUNj^1(oNbd4LV9Mb#8yJn<44S$0;T2V}|aK%lt)-ofJ#!Wd&V(%Nxw{2J<|F@A-a&UVN(rx{!jMWi1OnxD"
    "=YD{9MVwRh8EidI0~qlKF=Kx=EVONN&<KkwFoO@J%Jz<(8Y{n<Zh+z@xnx+DP8*7}}_+jjh}0%8jnwWGwCNSl$@o%?O`_wqj3cWq"
    "o>uOvp+#@U~MYDUIXF4pPF(=`qUXzpv}2ST=5!{3u)2yU*krMg55yS(~I5xj<JsKE+Ir!;+(5>BWd*^azgSRU2c0K(9HbuZY+NP3"
    "K|hVl10*lm)L@q51w<SUz?Xf#$5}*>C#5lJtO;<eO$CM`*^c7pxSmm=Yr>JcmwFS9}CmX@2BbX<jqSGx)mL5q0sjVt!01o~9J+*t"
    "9D6zF&wDoPr&kSC^9Rm1(7(v{*Hw^N(8HUsXzs;qYs-t4a%=V|mkA%U8%bUNz6|zpSc)_0V4=yAt+G{i=yh2iegr{$DgtF+9cc6h"
    "~8T>4ElEv%&d}F5_-Q&TuFB+$3@=&ry7J)Ckqo2-Wlmv)K_G&#UI?q4Q4hL)4v;mglGu<>(O{KZY}+K72ZGr|AjtUH!CViZog})N"
    "`~Pp_-!C1n#sR!QnexeMXoq#*N_k5uCtt4DSVWdVgm}6f(kX><AaIybIV7E?`HvfaMR?+|@^}Nsg1eZf-;mb#{bqBW&RKvqE?L@a"
    "Fje-pdh+X`T~!)dGKl?Jmd>96f@=chPhOr?OOr!_PtQM$ADY$I_F$yXMvxnxsaMMz~vjyo@hsj2-yQs2UM;Dm$h^ZbU(T1Z6}qBL"
    ";=47^GZQYVd2)%j8f)-?%RB|J=O4pPbL+z`F~_Xhnf70oG!87v})B1UMaa9NL1|gDr6SY74%c4ZMPJ0w!!1(Oynn{WuH&)i~6`&>"
    "))+GQ11JSWRFpZnEGBY{0Pv(QE}_hvYW{Tgxr5CETI#gW&DX``f{fzt^{Ca_}fX0_`71LBN*?>Ou~FPF<shzaVa4ll<E${{ux1v-"
    "<"
)
_CAMERA_CATALOG_ADDITIONS = (
    "apple,iPhone 18 Pro,8064,6048,,estimated_iphone17pro_proxy_not_verified\n"
    "apple,iPhone 18 Pro Max,8064,6048,,estimated_iphone17pro_proxy_not_verified\n"
)


@dataclass(frozen=True)
class LensAlias:
    brand: str
    source: str
    display: str


class ImageTooLargeError(RuntimeError):
    """Raised when an image would exceed the configured decode budget."""


@dataclass(frozen=True)
class DngUserCrop:
    """A DNG user crop in pixels of that file's default image area."""

    source_size: tuple[int, int]
    box: tuple[int, int, int, int]

    @property
    def size(self) -> tuple[int, int]:
        left, top, right, bottom = self.box
        return right - left, bottom - top


_PIL_DECOMPRESSION_BOMB_EXCEPTIONS: tuple[type[BaseException], ...] = tuple(
    exception_type
    for exception_type in (
        getattr(Image, "DecompressionBombError", None),
        getattr(Image, "DecompressionBombWarning", None),
    )
    if isinstance(exception_type, type)
)


def _lens_aliases(
    brand: str, aliases: tuple[tuple[str, str], ...]
) -> tuple[LensAlias, ...]:
    return tuple(
        LensAlias(brand=brand, source=source, display=display)
        for source, display in aliases
    )


LENS_NAME_ALIASES = (
    *_lens_aliases("sony", (
    # Sony FE zoom lenses
    ("FE 12-24mm F2.8 GM", "FE 12-24mm F2.8 GM"),
    ("FE 12-24mm F4 G", "FE 12-24mm F4 G"),
    ("FE 16-25mm F2.8 G", "FE 16-25mm F2.8 G"),
    ("FE 16-35mm F2.8 GM II", "FE 16-35mm F2.8 GM II"),
    ("FE 16-35mm F2.8 GM", "FE 16-35mm F2.8 GM"),
    ("FE PZ 16-35mm F4 G", "FE PZ 16-35mm F4 G"),
    ("FE C 16-35mm T3.1 G", "FE C 16-35mm T3.1 G"),
    ("FE 16-35mm F4 ZA OSS", "FE 16-35mm F4 ZA"),
    ("Vario-Tessar T* FE 16-35mm F4 ZA OSS", "FE 16-35mm F4 ZA"),
    ("FE 20-70mm F4 G", "FE 20-70mm F4 G"),
    ("FE 24-50mm F2.8 G", "FE 24-50mm F2.8 G"),
    ("FE 24-70mm F2.8 GM II", "FE 24-70mm F2.8 GM II"),
    ("FE 24-70mm F2.8 GM", "FE 24-70mm F2.8 GM"),
    ("FE 24-70mm F4 ZA OSS", "FE 24-70mm F4 ZA"),
    ("Vario-Tessar T* FE 24-70mm F4 ZA OSS", "FE 24-70mm F4 ZA"),
    ("FE 24-105mm F4 G OSS", "FE 24-105mm F4 G"),
    ("FE 24-240mm F3.5-6.3 OSS", "FE 24-240mm F3.5-6.3"),
    ("FE 28-60mm F4-5.6", "FE 28-60mm F4-5.6"),
    ("FE 28-70mm F2 GM", "FE 28-70mm F2 GM"),
    ("FE 28-70mm F3.5-5.6 OSS II", "FE 28-70mm F3.5-5.6 II"),
    ("FE 28-70mm F3.5-5.6 OSS", "FE 28-70mm F3.5-5.6"),
    ("FE PZ 28-135mm F4 G OSS", "FE PZ 28-135mm F4 G"),
    ("FE 50-150mm F2 GM", "FE 50-150mm F2 GM"),
    ("FE 70-200mm F2.8 GM OSS II", "FE 70-200mm F2.8 GM II"),
    ("FE 70-200mm F2.8 GM OSS", "FE 70-200mm F2.8 GM"),
    ("FE 70-200mm F4 Macro G OSS II", "FE 70-200mm F4 Macro G II"),
    ("FE 70-200mm F4 G OSS", "FE 70-200mm F4 G"),
    ("FE 70-300mm F4.5-5.6 G OSS", "FE 70-300mm F4.5-5.6 G"),
    ("FE 100-400mm F4.5-5.6 GM OSS", "FE 100-400mm F4.5-5.6 GM"),
    ("FE 200-600mm F5.6-6.3 G OSS", "FE 200-600mm F5.6-6.3 G"),
    ("FE 400-800mm F6.3-8 G OSS", "FE 400-800mm F6.3-8 G"),
    # Sony FE prime lenses
    ("FE 14mm F1.8 GM", "FE 14mm F1.8 GM"),
    ("FE 16mm F1.8 G", "FE 16mm F1.8 G"),
    ("FE 20mm F1.8 G", "FE 20mm F1.8 G"),
    ("FE 24mm F1.4 GM", "FE 24mm F1.4 GM"),
    ("FE 24mm F2.8 G", "FE 24mm F2.8 G"),
    ("FE 28mm F2", "FE 28mm F2"),
    ("FE 35mm F1.4 GM", "FE 35mm F1.4 GM"),
    ("FE 35mm F1.4 ZA", "FE 35mm F1.4 ZA"),
    ("Distagon T* FE 35mm F1.4 ZA", "FE 35mm F1.4 ZA"),
    ("FE 35mm F1.8", "FE 35mm F1.8"),
    ("FE 35mm F2.8 ZA", "FE 35mm F2.8 ZA"),
    ("Sonnar T* FE 35mm F2.8 ZA", "FE 35mm F2.8 ZA"),
    ("FE 40mm F2.5 G", "FE 40mm F2.5 G"),
    ("FE 50mm F1.2 GM", "FE 50mm F1.2 GM"),
    ("FE 50mm F1.4 GM", "FE 50mm F1.4 GM"),
    ("FE 50mm F1.4 ZA", "FE 50mm F1.4 ZA"),
    ("Planar T* FE 50mm F1.4 ZA", "FE 50mm F1.4 ZA"),
    ("FE 50mm F1.8", "FE 50mm F1.8"),
    ("FE 50mm F2.5 G", "FE 50mm F2.5 G"),
    ("FE 50mm F2.8 Macro", "FE 50mm F2.8 Macro"),
    ("FE 55mm F1.8 ZA", "FE 55mm F1.8 ZA"),
    ("Sonnar T* FE 55mm F1.8 ZA", "FE 55mm F1.8 ZA"),
    ("FE 85mm F1.4 GM II", "FE 85mm F1.4 GM II"),
    ("FE 85mm F1.4 GM", "FE 85mm F1.4 GM"),
    ("FE 85mm F1.8", "FE 85mm F1.8"),
    ("FE 90mm F2.8 Macro G OSS", "FE 90mm F2.8 Macro G"),
    ("FE 100mm F2.8 Macro GM OSS", "FE 100mm F2.8 Macro GM"),
    ("FE 100mm F2.8 STF GM OSS", "FE 100mm F2.8 STF GM"),
    ("FE 135mm F1.8 GM", "FE 135mm F1.8 GM"),
    ("FE 300mm F2.8 GM OSS", "FE 300mm F2.8 GM"),
    ("FE 400mm F2.8 GM OSS", "FE 400mm F2.8 GM"),
    ("FE 600mm F4 GM OSS", "FE 600mm F4 GM"),
    )),
    *_lens_aliases("sigma", (
    # Common SIGMA mirrorless and DSLR lenses
    ("SIGMA 10-18mm F2.8 DC DN Contemporary", "SIGMA 10-18mm F2.8 C"),
    ("SIGMA 12mm F1.4 DC Contemporary", "SIGMA 12mm F1.4 C"),
    ("SIGMA 14-24mm F2.8 DG DN Art", "SIGMA 14-24mm F2.8 Art"),
    ("SIGMA 14mm F1.4 DG DN Art", "SIGMA 14mm F1.4 Art"),
    ("SIGMA 14mm F1.4 DG Art", "SIGMA 14mm F1.4 Art"),
    ("SIGMA 14mm F1.8 DG HSM Art", "SIGMA 14mm F1.8 Art"),
    ("SIGMA 15mm F1.4 DC Contemporary", "SIGMA 15mm F1.4 C"),
    ("SIGMA 15mm F1.4 DG DN Diagonal Fisheye Art", "SIGMA 15mm F1.4 Fisheye Art"),
    ("SIGMA 16mm F1.4 DC DN Contemporary", "SIGMA 16mm F1.4 C"),
    ("SIGMA 16-28mm F2.8 DG DN Contemporary", "SIGMA 16-28mm F2.8 C"),
    ("SIGMA 17mm F4 DG DN Contemporary", "SIGMA 17mm F4 C"),
    ("SIGMA 17mm F4 DG Contemporary", "SIGMA 17mm F4 C"),
    ("SIGMA 18-35mm F1.8 DC HSM Art", "SIGMA 18-35mm F1.8 Art"),
    ("SIGMA 18-50mm F2.8 DC DN Contemporary", "SIGMA 18-50mm F2.8 C"),
    ("SIGMA 20-200mm F3.5-6.3 DG Contemporary", "SIGMA 20-200mm F3.5-6.3 C"),
    ("SIGMA 20-200mm F3.5-6.3 DG DN Contemporary", "SIGMA 20-200mm F3.5-6.3 C"),
    ("SIGMA 20mm F1.4 DG DN Art", "SIGMA 20mm F1.4 Art"),
    ("SIGMA 20mm F1.4 DG HSM Art", "SIGMA 20mm F1.4 Art"),
    ("SIGMA 20mm F2 DG DN Contemporary", "SIGMA 20mm F2 C"),
    ("SIGMA 20mm F2 DG Contemporary", "SIGMA 20mm F2 C"),
    ("SIGMA 23mm F1.4 DC DN Contemporary", "SIGMA 23mm F1.4 C"),
    ("SIGMA 24-35mm F2 DG HSM Art", "SIGMA 24-35mm F2 Art"),
    ("SIGMA 24-70mm F2.8 DG DN II Art", "SIGMA 24-70mm F2.8 Art II"),
    ("SIGMA 24-70mm F2.8 DG DN Art", "SIGMA 24-70mm F2.8 Art"),
    ("SIGMA 24-70mm F2.8 DG OS HSM Art", "SIGMA 24-70mm F2.8 Art"),
    ("SIGMA 24-105mm F4 DG OS HSM Art", "SIGMA 24-105mm F4 Art"),
    ("SIGMA 24mm F1.4 DG DN Art", "SIGMA 24mm F1.4 Art"),
    ("SIGMA 24mm F1.4 DG HSM Art", "SIGMA 24mm F1.4 Art"),
    ("SIGMA 24mm F2 DG DN Contemporary", "SIGMA 24mm F2 C"),
    ("SIGMA 24mm F2 DG Contemporary", "SIGMA 24mm F2 C"),
    ("SIGMA 24mm F3.5 DG DN Contemporary", "SIGMA 24mm F3.5 C"),
    ("SIGMA 24mm F3.5 DG Contemporary", "SIGMA 24mm F3.5 C"),
    ("SIGMA 28-45mm F1.8 DG DN Art", "SIGMA 28-45mm F1.8 Art"),
    ("SIGMA 28-70mm F2.8 DG DN Contemporary", "SIGMA 28-70mm F2.8 C"),
    ("SIGMA 28-105mm F2.8 DG DN Art", "SIGMA 28-105mm F2.8 Art"),
    ("SIGMA 28mm F1.4 DG HSM Art", "SIGMA 28mm F1.4 Art"),
    ("SIGMA 30mm F1.4 DC DN Contemporary", "SIGMA 30mm F1.4 C"),
    ("SIGMA 35mm F1.2 DG DN II Art", "SIGMA 35mm F1.2 Art II"),
    ("SIGMA 35mm F1.2 DG DN Art", "SIGMA 35mm F1.2 Art"),
    ("SIGMA 35mm F1.4 DG DN II Art", "SIGMA 35mm F1.4 Art II"),
    ("SIGMA 35mm F1.4 DG DN Art", "SIGMA 35mm F1.4 Art"),
    ("SIGMA 35mm F1.4 DG HSM Art", "SIGMA 35mm F1.4 Art"),
    ("SIGMA 35mm F2 DG DN Contemporary", "SIGMA 35mm F2 C"),
    ("SIGMA 35mm F2 DG Contemporary", "SIGMA 35mm F2 C"),
    ("SIGMA 40mm F1.4 DG HSM Art", "SIGMA 40mm F1.4 Art"),
    ("SIGMA 45mm F2.8 DG DN Contemporary", "SIGMA 45mm F2.8 C"),
    ("SIGMA 45mm F2.8 DG Contemporary", "SIGMA 45mm F2.8 C"),
    ("SIGMA 50-100mm F1.8 DC HSM Art", "SIGMA 50-100mm F1.8 Art"),
    ("SIGMA 50mm F1.2 DG DN Art", "SIGMA 50mm F1.2 Art"),
    ("SIGMA 50mm F1.4 DG DN Art", "SIGMA 50mm F1.4 Art"),
    ("SIGMA 50mm F1.4 DG HSM Art", "SIGMA 50mm F1.4 Art"),
    ("SIGMA 50mm F2 DG DN Contemporary", "SIGMA 50mm F2 C"),
    ("SIGMA 50mm F2 DG Contemporary", "SIGMA 50mm F2 C"),
    ("SIGMA 56mm F1.4 DC DN Contemporary", "SIGMA 56mm F1.4 C"),
    ("SIGMA 60-600mm F4.5-6.3 DG DN OS Sports", "SIGMA 60-600mm F4.5-6.3 Sports"),
    ("SIGMA 60-600mm F4.5-6.3 DG OS HSM Sports", "SIGMA 60-600mm F4.5-6.3 Sports"),
    ("SIGMA 65mm F2 DG DN Contemporary", "SIGMA 65mm F2 C"),
    ("SIGMA 65mm F2 DG Contemporary", "SIGMA 65mm F2 C"),
    ("SIGMA 70-200mm F2.8 DG DN OS Sports", "SIGMA 70-200mm F2.8 Sports"),
    ("SIGMA 70-200mm F2.8 DG OS HSM Sports", "SIGMA 70-200mm F2.8 Sports"),
    ("SIGMA 70mm F2.8 DG Macro Art", "SIGMA 70mm F2.8 Macro Art"),
    ("SIGMA 85mm F1.4 DG DN Art", "SIGMA 85mm F1.4 Art"),
    ("SIGMA 85mm F1.4 DG HSM Art", "SIGMA 85mm F1.4 Art"),
    ("SIGMA 90mm F2.8 DG DN Contemporary", "SIGMA 90mm F2.8 C"),
    ("SIGMA 90mm F2.8 DG Contemporary", "SIGMA 90mm F2.8 C"),
    ("SIGMA 100-400mm F5-6.3 DG DN OS Contemporary", "SIGMA 100-400mm F5-6.3 C"),
    ("SIGMA 100-400mm F5-6.3 DG OS HSM Contemporary", "SIGMA 100-400mm F5-6.3 C"),
    ("SIGMA 105mm F1.4 DG HSM Art", "SIGMA 105mm F1.4 Art"),
    ("SIGMA 105mm F2.8 DG DN Macro Art", "SIGMA 105mm F2.8 Macro Art"),
    ("SIGMA 120-300mm F2.8 DG OS HSM Sports", "SIGMA 120-300mm F2.8 Sports"),
    ("SIGMA 135mm F1.4 DG Art", "SIGMA 135mm F1.4 Art"),
    ("SIGMA 135mm F1.8 DG HSM Art", "SIGMA 135mm F1.8 Art"),
    ("SIGMA 150-600mm F5-6.3 DG DN OS Sports", "SIGMA 150-600mm F5-6.3 Sports"),
    ("SIGMA 150-600mm F5-6.3 DG OS HSM Contemporary", "SIGMA 150-600mm F5-6.3 C"),
    ("SIGMA 150-600mm F5-6.3 DG OS HSM Sports", "SIGMA 150-600mm F5-6.3 Sports"),
    ("SIGMA 200mm F2 DG OS Sports", "SIGMA 200mm F2 Sports"),
    ("SIGMA 500mm F5.6 DG DN OS Sports", "SIGMA 500mm F5.6 Sports"),
    ("SIGMA 500mm F4 DG OS HSM Sports", "SIGMA 500mm F4 Sports"),
    )),
    *_lens_aliases("tamron", (
    # Common TAMRON full-frame Sony E lenses
    ("Tamron 12-20mm F2.8", "12-20 F2.8"),
    ("E 12-20mm F2.8", "12-20 F2.8"),
    ("Tamron 16-30mm F2.8 Di III VXD G2", "16-30 F2.8 G2"),
    ("E 16-30mm F2.8 A064", "16-30 F2.8 G2"),
    ("Tamron 17-28mm F2.8 Di III RXD", "17-28 F2.8"),
    ("E 17-28mm F2.8 A046", "17-28 F2.8"),
    ("Tamron 17-50mm F4 Di III VXD", "17-50 F4"),
    ("E 17-50mm F4 A068", "17-50 F4"),
    ("Tamron 20-40mm F2.8 Di III VXD", "20-40 F2.8"),
    ("E 20-40mm F2.8 A062", "20-40 F2.8"),
    ("Tamron 20mm F2.8 Di III OSD M1:2", "20 F2.8"),
    ("E 20mm F2.8 F050", "20 F2.8"),
    ("Tamron 24mm F2.8 Di III OSD M1:2", "24 F2.8"),
    ("E 24mm F2.8 F051", "24 F2.8"),
    ("Tamron 25-200mm F2.8-5.6 Di III VXD G2", "25-200 F2.8-5.6 G2"),
    ("E 25-200mm F2.8-5.6 A075", "25-200 F2.8-5.6 G2"),
    ("Tamron 28-75mm F2.8 Di III VXD G2", "28-75 F2.8 G2"),
    ("E 28-75mm F2.8 A063", "28-75 F2.8 G2"),
    ("Tamron 28-75mm F2.8 Di III RXD", "28-75 F2.8"),
    ("E 28-75mm F2.8 A036", "28-75 F2.8"),
    ("Tamron 28-200mm F2.8-5.6 Di III RXD", "28-200 F2.8-5.6"),
    ("E 28-200mm F2.8-5.6 A071", "28-200 F2.8-5.6"),
    ("Tamron 28-300mm F4-7.1 Di III VC VXD", "28-300 F4-7.1"),
    ("E 28-300mm F4-7.1 A074", "28-300 F4-7.1"),
    ("Tamron 35mm F2.8 Di III OSD M1:2", "35 F2.8"),
    ("E 35mm F2.8 F053", "35 F2.8"),
    ("Tamron 35-100mm F2.8 Di III VXD", "35-100 F2.8"),
    ("E 35-100mm F2.8 A078", "35-100 F2.8"),
    ("Tamron 35-150mm F2-2.8 Di III VXD", "35-150 F2-2.8"),
    ("E 35-150mm F2-2.8 A058", "35-150 F2-2.8"),
    ("Tamron 50-300mm F4.5-6.3 Di III VC VXD", "50-300 F4.5-6.3"),
    ("E 50-300mm F4.5-6.3 A069", "50-300 F4.5-6.3"),
    ("Tamron 50-400mm F4.5-6.3 Di III VC VXD", "50-400 F4.5-6.3"),
    ("E 50-400mm F4.5-6.3 A067", "50-400 F4.5-6.3"),
    ("Tamron 70-180mm F2.8 Di III VC VXD G2", "70-180 F2.8 G2"),
    ("70-180mm F2.8 Di III VC VXD G2", "70-180 F2.8 G2"),
    ("E 70-180mm F2.8 A065", "70-180 F2.8 G2"),
    ("Tamron 70-180mm F2.8 Di III VXD", "70-180 F2.8"),
    ("Tamron 70-180mm F2.8 Di III RXD", "70-180 F2.8"),
    ("E 70-180mm F2.8 A056", "70-180 F2.8"),
    ("Tamron 70-300mm F4.5-6.3 Di III RXD", "70-300 F4.5-6.3"),
    ("E 70-300mm F4.5-6.3 A047", "70-300 F4.5-6.3"),
    ("Tamron 90mm F2.8 Di III Macro VXD", "90 F2.8 Macro"),
    ("E 90mm F2.8 F072", "90 F2.8 Macro"),
    ("Tamron 150-500mm F5-6.7 Di III VC VXD", "150-500 F5-6.7"),
    ("E 150-500mm F5-6.7 A057", "150-500 F5-6.7"),
    )),
)
APPLE_LENS_SPECS = (
    (Decimal("1.54"), Decimal("2.4"), "Ultra Wide"),
    (Decimal("1.55"), Decimal("2.4"), "Ultra Wide"),
    (Decimal("1.57"), Decimal("1.8"), "Ultra Wide"),
    (Decimal("2.22"), Decimal("2.2"), "Ultra Wide"),
    (Decimal("2.69"), Decimal("1.9"), "Front"),
    (Decimal("2.71"), Decimal("1.9"), "Front"),
    (Decimal("3.99"), Decimal("1.8"), "Wide"),
    (Decimal("4.2"), Decimal("1.6"), "Wide"),
    (Decimal("5.1"), Decimal("1.6"), "Wide"),
    (Decimal("5.7"), Decimal("1.5"), "Wide"),
    (Decimal("5.96"), Decimal("1.6"), "Wide"),
    (Decimal("6.0"), Decimal("2.0"), "Tele"),
    (Decimal("6.765"), Decimal("1.78"), "Main"),
    (Decimal("6.86"), Decimal("1.78"), "Main"),
    (Decimal("7.0"), Decimal("1.6"), "Wide"),
    (Decimal("9.0"), Decimal("2.8"), "Tele"),
    (Decimal("15.66"), Decimal("2.8"), "Tele"),
    # Verified in Apple's iPhone 18 Pro technical specifications:
    # https://www.apple.com/iphone-18-pro/specs/
    (Decimal("13"), Decimal("2.2"), "Ultra Wide"),
    (Decimal("24"), Decimal("1.48"), "Main"),
    (Decimal("24"), Decimal("1.8"), "Main"),
    (Decimal("24"), Decimal("2.8"), "Main"),
    (Decimal("24"), Decimal("4.0"), "Main"),
    (Decimal("100"), Decimal("2.8"), "Tele"),
    (Decimal("200"), Decimal("2.8"), "Tele"),
)
FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/arial.ttf",
)
FONT_BOLD_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/msjhbd.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_api_base_url: str
    local_mode: bool
    signature_image_dir: Path
    signature_image_file: str
    jpeg_quality: int
    max_file_size_mb: int
    file_transfer_timeout_seconds: int
    max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS

    @property
    def signature_image_path(self) -> Path:
        return self.signature_image_dir / self.signature_image_file

    @classmethod
    def from_environment(cls) -> "Settings":
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

        quality = int(os.getenv("JPEG_QUALITY", "95"))
        if not 80 <= quality <= 100:
            raise RuntimeError("JPEG_QUALITY must be between 80 and 100")

        try:
            max_image_pixels = int(
                os.getenv("MAX_IMAGE_PIXELS", str(DEFAULT_MAX_IMAGE_PIXELS))
            )
        except ValueError as exc:
            raise RuntimeError("MAX_IMAGE_PIXELS must be an integer") from exc
        if max_image_pixels <= 0:
            raise RuntimeError("MAX_IMAGE_PIXELS must be greater than zero")

        return cls(
            bot_token=bot_token,
            bot_api_base_url=os.getenv(
                "TELEGRAM_BOT_API_URL", "https://api.telegram.org/bot"
            ).rstrip("/"),
            local_mode=os.getenv("TELEGRAM_LOCAL_MODE", "false").lower()
            in {"1", "true", "yes"},
            signature_image_dir=Path(
                os.getenv("SIGNATURE_IMAGE_DIR", "/app/assets")
            ),
            signature_image_file=os.getenv(
                "SIGNATURE_IMAGE_FILE", "signature.png"
            ).strip(),
            jpeg_quality=quality,
            max_file_size_mb=int(os.getenv("MAX_FILE_SIZE_MB", "20")),
            max_image_pixels=max_image_pixels,
            file_transfer_timeout_seconds=int(
                os.getenv("FILE_TRANSFER_TIMEOUT_SECONDS", "300")
            ),
        )


_DECIMAL_TOKEN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_APERTURE_RANGE_RE = re.compile(
    rf"^\s*(?:[fƒ]\s*/?\s*)?(?P<first>{_DECIMAL_TOKEN})"
    rf"(?:\s*[-–—]\s*(?:[fƒ]\s*/?\s*)?"
    rf"(?P<second>{_DECIMAL_TOKEN}))?\s*$",
    flags=re.I,
)
_VARIABLE_APERTURE_MIN = Decimal("1.48")
_APERTURE_RANGE_SEPARATOR = "-"


def _decimal_scalar(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("boolean is not a numeric EXIF value")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # str() keeps the decimal value represented by the EXIF input instead
        # of importing the float's binary approximation into Decimal.
        return Decimal(str(value))
    return Decimal(str(value).strip())


def _decimal_ratio(numerator: Any, denominator: Any) -> Decimal:
    numerator_decimal = _decimal_scalar(numerator)
    denominator_decimal = _decimal_scalar(denominator)
    if denominator_decimal == 0:
        raise ZeroDivisionError("EXIF rational denominator is zero")
    return numerator_decimal / denominator_decimal


def _as_decimal(value: Any) -> Decimal | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, list) and len(value) == 1:
            return _as_decimal(value[0])
        if isinstance(value, tuple) and len(value) == 2:
            return _decimal_ratio(value[0], value[1])
        if hasattr(value, "num") and hasattr(value, "den"):
            return _decimal_ratio(value.num, value.den)
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            return _decimal_ratio(value.numerator, value.denominator)
        if isinstance(value, bytes) or isinstance(value, (list, tuple)):
            value = _clean_text(value)
        if isinstance(value, str):
            text = value.strip()
            fraction_match = re.search(
                rf"(?P<num>{_DECIMAL_TOKEN})\s*/\s*(?P<den>{_DECIMAL_TOKEN})",
                text,
            )
            if fraction_match:
                return _decimal_ratio(
                    fraction_match.group("num"), fraction_match.group("den")
                )
            number_match = re.search(_DECIMAL_TOKEN, text)
            if number_match:
                return _decimal_scalar(number_match.group(0))
        result = _decimal_scalar(value)
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return None


def _as_float(value: Any) -> float | None:
    decimal_value = _as_decimal(value)
    if decimal_value is None:
        return None
    try:
        result = float(decimal_value)
    except (OverflowError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _read_exif(image: Image.Image) -> dict[str, Any]:
    exif = image.getexif()
    values = {
        ExifTags.TAGS.get(tag_id, str(tag_id)): value
        for tag_id, value in exif.items()
    }

    try:
        values.update(
            {
                ExifTags.TAGS.get(tag_id, str(tag_id)): value
                for tag_id, value in exif.get_ifd(EXIF_IFD).items()
            }
        )
    except (AttributeError, KeyError, TypeError):
        pass

    return values


def _exifread_value(value: Any) -> Any:
    tag_values = getattr(value, "values", None)
    if isinstance(tag_values, list) and len(tag_values) == 1:
        return tag_values[0]
    if tag_values not in (None, ""):
        return tag_values
    return str(value)


def _dng_tag_id(key: str, value: Any) -> int | None:
    tag_id = getattr(value, "tag", None)
    if isinstance(tag_id, int):
        return tag_id
    match = re.search(r" Tag 0x([0-9A-Fa-f]{4})$", key)
    return int(match.group(1), 16) if match else None


def _dng_tag_values(tags: dict[str, Any], prefix: str, tag_id: int) -> Any:
    for key, value in tags.items():
        if key.startswith(f"{prefix} ") and _dng_tag_id(key, value) == tag_id:
            return getattr(value, "values", None)
    return None


def _dng_user_crops(tags: dict[str, Any]) -> tuple[DngUserCrop, ...]:
    # DNG 1.7.1: DefaultCropSize (50720) is the source image area and
    # DefaultUserCrop (51125) is a relative user crop within that area.
    # https://helpx.adobe.com/content/dam/help/en/camera-raw/digital-negative/jcr_content/root/content/flex/items/position/position-par/download_section_733958301/download-1/DNG_Spec_1_7_1_0.pdf
    # EXIFread 3.5.1 exposes these currently unnamed TIFF tags by hex ID.
    version = _dng_tag_values(tags, "Image", 50706)
    if (
        not isinstance(version, (list, tuple))
        or len(version) != 4
        or any(not isinstance(value, int) or not 0 <= value <= 255 for value in version)
        or tuple(version) < (1, 4, 0, 0)
    ):
        return ()

    prefixes = {
        key.rsplit(" Tag 0x", 1)[0]
        if " Tag 0x" in key
        else key.rpartition(" ")[0]
        for key, value in tags.items()
        if _dng_tag_id(key, value) == 50720
    }
    crops: set[DngUserCrop] = set()
    for prefix in prefixes:
        if prefix != "Image" and not re.fullmatch(r"EXIF SubIFD\d+", prefix):
            continue
        dimensions = _dng_tag_values(tags, prefix, 50720)
        user_crop = _dng_tag_values(tags, prefix, 51125)
        default_scale = _dng_tag_values(tags, prefix, 50718)
        if not isinstance(dimensions, (list, tuple)) or len(dimensions) != 2:
            continue
        if not isinstance(user_crop, (list, tuple)) or len(user_crop) != 4:
            continue
        if default_scale is not None and (
            not isinstance(default_scale, (list, tuple))
            or len(default_scale) != 2
            or any(_as_decimal(value) != 1 for value in default_scale)
        ):
            continue

        width_value, height_value = (_as_decimal(v) for v in dimensions)
        if (
            width_value is None
            or height_value is None
            or width_value != width_value.to_integral_value()
            or height_value != height_value.to_integral_value()
            or width_value <= 0
            or height_value <= 0
        ):
            continue
        width, height = int(width_value), int(height_value)
        bounds = [_as_decimal(value) for value in user_crop]
        if any(value is None or not value.is_finite() for value in bounds):
            continue
        top, left, bottom, right = bounds
        if not (0 <= top < bottom <= 1 and 0 <= left < right <= 1):
            continue

        coordinates = (left * width, top * height, right * width, bottom * height)
        if any(value != value.to_integral_value() for value in coordinates):
            continue
        box = tuple(int(value) for value in coordinates)
        crop = DngUserCrop((width, height), box)
        if crop.size != crop.source_size:
            crops.add(crop)
    return tuple(sorted(crops, key=lambda crop: (crop.source_size, crop.box)))


def _read_raw_exif(
    image_source: bytes | Path, original_name: str | None = None
) -> dict[str, Any]:
    is_dng = _source_extension(image_source, original_name) == "dng"
    try:
        if isinstance(image_source, Path):
            with image_source.open("rb") as handle:
                tags = exifread.process_file(
                    handle, details=is_dng, extract_thumbnail=False
                )
        else:
            tags = exifread.process_file(
                BytesIO(image_source), details=is_dng, extract_thumbnail=False
            )
    except Exception:
        LOGGER.warning("Unable to read RAW EXIF metadata", exc_info=True)
        return {}

    values = {
        target: _exifread_value(tags[source])
        for source, target in EXIFREAD_TAGS.items()
        if source in tags
    }
    if is_dng:
        crops = _dng_user_crops(tags)
        if crops:
            values["DngUserCrops"] = crops
    return values


def _decode_text_bytes(value: bytes) -> str:
    raw = value.strip()
    if not raw:
        return ""

    encodings: list[str] = []
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    elif len(raw) > 1:
        even_nuls = raw[::2].count(0)
        odd_nuls = raw[1::2].count(0)
        nul_threshold = max(2, len(raw) // 4)
        if odd_nuls >= nul_threshold and odd_nuls > even_nuls:
            encodings.append("utf-16le")
        elif even_nuls >= nul_threshold and even_nuls > odd_nuls:
            encodings.append("utf-16be")

    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        pass

    encodings.extend(
        ("utf-16le", "utf-16be", "cp950", "gb18030", "shift_jis", "cp1252")
    )
    candidates: list[tuple[float, int, str]] = []
    for encoding in dict.fromkeys(encodings):
        try:
            text = value.decode(encoding).replace("\ufeff", "")
        except UnicodeDecodeError:
            continue
        candidates.append((_decoded_text_score(text), -len(candidates), text))
    if candidates:
        return max(candidates)[2]
    return value.decode("utf-8", errors="replace")


def _decoded_text_score(value: str) -> float:
    score = 0.0
    for char in value.replace("\x00", ""):
        codepoint = ord(char)
        category = unicodedata.category(char)
        if char.isspace():
            score += 0.2
        elif category.startswith("C"):
            score -= 8
        elif 0xE000 <= codepoint <= 0xF8FF or 0xF900 <= codepoint <= 0xFAFF:
            score -= 4
        elif 0xFF00 <= codepoint <= 0xFFEF:
            score -= 2
        elif 0xAC00 <= codepoint <= 0xD7AF:
            score -= 1.5
        elif 0x0080 <= codepoint <= 0x024F:
            score -= 2
        elif 0x4E00 <= codepoint <= 0x9FFF:
            score += 2
        elif char.isascii() and (char.isalnum() or char in " -_./:+()[]#"):
            score += 2
        elif char.isprintable() and category[0] in {"L", "N", "P", "S"}:
            score += 1
        else:
            score -= 1
    return score


def _clean_text(value: Any) -> str:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, int) and 0 <= item <= 255 for item in value
    ) and (
        len(value) > 4 and (0 in value or any(item > 127 for item in value))
    ):
        value = bytes(value)
    if isinstance(value, bytes):
        value = _decode_text_bytes(value)
    return " ".join(str(value).replace("\x00", "").split())


def _lens_match_key(value: str) -> str:
    text = value.replace("–", "-").replace("—", "-")
    text = text.replace("|", " ")
    text = re.sub(r"\bF\s*/\s*", "F", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def _strip_sony_fe_prefix(value: str) -> str:
    return re.sub(r"^FE\s+", "", value).strip()


def _format_sigma_lens_name(value: str, *, require_brand: bool = True) -> str | None:
    if require_brand and "sigma" not in value.casefold():
        return None

    cleaned = value.replace("–", "-").replace("—", "-")
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\bSIGMA\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bLens\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bfor\s+\w+\s+mount\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bfor\s+Sony\s+E\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bE-mount\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bF\s*/\s*", "F", cleaned, flags=re.I)
    cleaned = re.sub(r"\bDiagonal\s+Fisheye\b", "Fisheye", cleaned, flags=re.I)
    cleaned = re.sub(r"\bMACRO\b", "Macro", cleaned)
    cleaned = re.sub(r"\bContemporary\b", "C", cleaned)
    cleaned = re.sub(
        r"\b(DG|DC|DN|HSM|OS|EX|APO|DL|UC|DN|ASP|Aspherical)\b",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\bII\s+(Art|C|Sports)\b", r"\1 II", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned or None


def _format_lens_display(alias: LensAlias) -> str:
    if alias.brand == "sony":
        return _strip_sony_fe_prefix(alias.display)
    if alias.brand == "sigma":
        return _format_sigma_lens_name(alias.display, require_brand=False) or alias.display
    if alias.brand == "tamron":
        return re.sub(r"^([0-9]+(?:-[0-9]+)?)\b", r"\1mm", alias.display)
    return alias.display


def _quantize_positive(value: Any, quantum: Decimal) -> Decimal | None:
    decimal_value = _as_decimal(value)
    if (
        decimal_value is None
        or not decimal_value.is_finite()
        or decimal_value <= 0
    ):
        return None

    try:
        # Give quantize enough precision for large but valid EXIF values while
        # keeping ROUND_HALF_UP explicit and independent of float behavior.
        digits = len(decimal_value.as_tuple().digits)
        adjusted = max(decimal_value.adjusted(), 0)
        with localcontext() as context:
            context.prec = max(28, digits + adjusted + 4)
            rounded = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, OverflowError):
        return None

    return rounded if rounded.is_finite() and rounded > 0 else None


def _decimal_display(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_aperture(value: Any) -> tuple[Decimal, Decimal | None] | None:
    if isinstance(value, bytes):
        value = _clean_text(value)

    if isinstance(value, str):
        match = _APERTURE_RANGE_RE.fullmatch(value)
        if match:
            first = _as_decimal(match.group("first"))
            second = (
                _as_decimal(match.group("second"))
                if match.group("second") is not None
                else None
            )
            if first is not None and (
                second is None or second >= first
            ):
                return first, second

    scalar = _as_decimal(value)
    return (scalar, None) if scalar is not None else None


def _format_aperture_scalar(value: Decimal) -> str | None:
    rounded = _quantize_positive(value, Decimal("0.1"))
    if rounded is None:
        return None

    if value == _VARIABLE_APERTURE_MIN:
        exact_min = _quantize_positive(value, Decimal("0.01"))
        if exact_min is not None:
            return f"F{_decimal_display(exact_min)}"

    return f"F{_decimal_display(rounded)}"


def _format_aperture(value: Any) -> str | None:
    parsed = _parse_aperture(value)
    if parsed is None:
        return None

    first, second = parsed
    first_display = _format_aperture_scalar(first)
    if first_display is None:
        return None
    if second is None:
        return first_display

    second_display = _format_aperture_scalar(second)
    if second_display is None:
        return None
    return f"{first_display}{_APERTURE_RANGE_SEPARATOR}{second_display}"


def _format_focal_length(value: Any, *, unit: str = "MM") -> str | None:
    rounded = _quantize_positive(value, Decimal("1"))
    if rounded is None:
        return None
    return f"{_decimal_display(rounded)}{unit}"


def _apple_lens_role(focal_mm: Any, aperture: Any, side: str) -> str:
    focal_decimal = _as_decimal(focal_mm)
    aperture_decimal = _as_decimal(aperture)
    if focal_decimal is None or aperture_decimal is None:
        return "Front" if side.casefold() == "front" else "Back"

    for spec_focal, spec_aperture, role in APPLE_LENS_SPECS:
        focal_matches = abs(focal_decimal - spec_focal) <= Decimal("0.035")
        aperture_matches = (
            abs(aperture_decimal - spec_aperture) <= Decimal("0.035")
        )
        if focal_matches and aperture_matches:
            return role
    return "Front" if side.casefold() == "front" else "Back"


def _format_apple_lens_name(value: str) -> str | None:
    if "iphone" not in value.casefold():
        return None

    match = re.search(
        r"\b(?P<side>front|back)\b.*?\bcamera\b.*?"
        r"(?P<focal>[0-9]+(?:\.[0-9]+)?)\s*mm\s*"
        rf"(?P<aperture_spec>[fƒ]\s*/?\s*"
        rf"(?P<aperture_start>{_DECIMAL_TOKEN})"
        rf"(?:\s*[-–—]\s*(?:[fƒ]\s*/?\s*)?"
        rf"(?P<aperture_end>{_DECIMAL_TOKEN}))?)",
        value,
        flags=re.I,
    )
    if not match:
        return None

    focal_mm = _as_decimal(match.group("focal"))
    aperture = _as_decimal(match.group("aperture_start"))
    if focal_mm is None or aperture is None:
        return None

    role = _apple_lens_role(focal_mm, aperture, match.group("side"))
    formatted_focal = _format_focal_length(focal_mm, unit="mm")
    formatted_aperture = _format_aperture(match.group("aperture_spec"))
    if formatted_focal is None or formatted_aperture is None:
        return None
    return f"{role} {formatted_focal} {formatted_aperture}"


def _match_lens_alias(value: Any) -> LensAlias | None:
    original = _clean_text(value)
    match_key = _lens_match_key(original)
    for alias in sorted(
        LENS_NAME_ALIASES, key=lambda item: len(item.source), reverse=True
    ):
        source_key = _lens_match_key(alias.source)
        source_without_brand = re.sub(
            r"^(sigma|tamron|sony)\s+", "", source_key
        )
        if source_key in match_key or source_without_brand in match_key:
            return alias
    return None


def _lens_badge_key_from_alias(alias: LensAlias | None) -> str | None:
    if alias is None:
        return None
    if alias.brand in {"sigma", "tamron"}:
        return alias.brand
    if alias.brand == "sony":
        if re.search(r"\bGM\b", alias.display):
            return "gm"
        if re.search(r"\bG\b", alias.display):
            return "g"
    return None


def _format_lens_name(value: Any) -> str:
    original = _clean_text(value)
    if not original:
        return "UNKNOWN LENS"

    apple_lens = _format_apple_lens_name(original)
    if apple_lens:
        return apple_lens

    alias = _match_lens_alias(original)
    if alias:
        return _format_lens_display(alias)

    sigma_lens = _format_sigma_lens_name(original)
    if sigma_lens:
        return sigma_lens

    cleaned = re.sub(r"\b(Sony|Lens|E-mount|for Sony E)\b", "", original, flags=re.I)
    cleaned = re.sub(r"\bOptical SteadyShot\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(DG DN|DG HSM|DG OS|Di III|VC|VXD|RXD|OSD)\b", "", cleaned)
    cleaned = re.sub(r"\bContemporary\b", "C", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned.replace("|", " ")).strip(" -")
    cleaned = _strip_sony_fe_prefix(cleaned)
    return cleaned or original


def _format_exposure(value: Any) -> str | None:
    seconds = _as_float(value)
    if seconds is None or seconds <= 0:
        return None
    if seconds < 1:
        return f"1/{round(1 / seconds)}s"
    return f"{seconds:g}s"


@dataclass(frozen=True)
class PhotoMetadata:
    iso: str
    aperture: str
    focal_length_35mm: str
    focal_length_physical: str
    focal_length_cropped: str
    shutter_speed: str
    camera_make: str
    camera_model: str
    lens: str
    lens_badge_key: str | None
    captured_at: str
    focal_length_cropped_basis: str | None = None


def _format_capture_time(value: Any) -> str:
    if value is None:
        return "DATE UNKNOWN"
    text = _clean_text(value)
    match = re.search(
        r"\b(?P<year>\d{4})[:/-](?P<month>\d{2})[:/-](?P<day>\d{2})"
        r"[ T]+(?P<hour>\d{2}):(?P<minute>\d{2})",
        text,
    )
    if not match:
        return "DATE UNKNOWN"
    return (
        f"{match.group('year')}.{match.group('month')}.{match.group('day')}  "
        f"{match.group('hour')}:{match.group('minute')}"
    )


def _capture_time_from_exif(exif: dict[str, Any]) -> str:
    for tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        captured_at = _format_capture_time(exif.get(tag))
        if captured_at != "DATE UNKNOWN":
            return captured_at
    return "DATE UNKNOWN"


def _camera_brand_key(make: str, model: str) -> str | None:
    for source in (model.casefold(), make.casefold()):
        for key in BRAND_NAMES:
            if key in source:
                return key
    return None


def _strip_camera_make(make: str, model: str, brand_key: str | None) -> str:
    prefixes = [make]
    if brand_key:
        prefixes.extend((brand_key, BRAND_NAMES[brand_key]))

    result = model.strip()
    for prefix in sorted(set(prefixes), key=len, reverse=True):
        if prefix and result.casefold().startswith(prefix.casefold()):
            stripped = result[len(prefix) :].lstrip(" -_/")
            if stripped:
                result = stripped
                break
    return result or "UNKNOWN MODEL"


@dataclass(frozen=True)
class CameraResolution:
    """A documented maximum still size, not this frame's known capture size."""

    brand: str
    model: str
    width: int
    height: int
    source: str


_CAMERA_MAKE_ALIASES = {
    "sony": ("sony",),
    "sigma": ("sigma",),
    "nikon": ("nikon",),
    "canon": ("canon",),
    "leica": ("leica",),
    "apple": ("apple",),
    "ricoh": ("ricoh",),
    "fujifilm": ("fujifilm", "fuji film"),
    "panasonic": ("panasonic", "lumix"),
    "olympus": ("olympus",),
    "om digital": ("om digital", "om system"),
    "pentax": ("pentax", "asahi pentax"),
    "hasselblad": ("hasselblad",),
}
# These identifier-to-product mappings are supported by ExifTool's Sony model
# documentation: https://exiftool.org/TagNames/Sony.html
_SONY_EXIF_MODEL_ALIASES = {
    "ilce7m4": "a7iv",
    "ilce7rm5": "a7rv",
    "ilce7sm3": "a7siii",
    "ilce1": "a1",
}


def _camera_name_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("α", "a")
    return "".join(character for character in value if character.isalnum())


def _catalog_brand(make: str, model: str) -> str | None:
    make_key = _camera_name_key(make)
    model_key = _camera_name_key(model)
    matches = [
        brand
        for brand, aliases in _CAMERA_MAKE_ALIASES.items()
        if any(make_key.startswith(_camera_name_key(alias)) for alias in aliases)
    ]
    if len(matches) > 1:
        return None
    if matches:
        brand = matches[0]
        if brand == "olympus" and (
            model_key.startswith("omsystem")
            or model_key.startswith("omtough")
            or re.match(r"^om[135](?:mark|ii|$)", model_key)
        ):
            return "om digital"
        return brand
    # A missing or unrecognized Make is safe only when Model names one brand.
    model_matches = [
        brand
        for brand, aliases in _CAMERA_MAKE_ALIASES.items()
        if any(model_key.startswith(_camera_name_key(alias)) for alias in aliases)
    ]
    return model_matches[0] if len(model_matches) == 1 else None


def _catalog_model_key(brand: str, model: str) -> str:
    key = _camera_name_key(model)
    aliases = _CAMERA_MAKE_ALIASES[brand]
    prefixes = sorted((_camera_name_key(a) for a in aliases), key=len, reverse=True)
    while key:
        prefix = next((value for value in prefixes if key.startswith(value)), None)
        if prefix is None or len(prefix) == len(key):
            break
        key = key[len(prefix) :]
    if brand == "sony":
        key = _SONY_EXIF_MODEL_ALIASES.get(key, key)
    return key


@lru_cache(maxsize=1)
def _camera_resolution_catalog() -> dict[tuple[str, str], tuple[CameraResolution, ...]]:
    body = (
        zlib.decompress(base64.b85decode(_CAMERA_CATALOG_B85)).decode("utf-8")
        + _CAMERA_CATALOG_ADDITIONS
    )
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != _CAMERA_CATALOG_SHA256:
        raise RuntimeError("Camera resolution catalogue failed its integrity check")
    index: dict[tuple[str, str], list[CameraResolution]] = {}
    for row in csv.DictReader(StringIO(body)):
        brand = row["brand"]
        resolution = CameraResolution(
            brand, row["model"], int(row["width"]), int(row["height"]), row["source"]
        )
        if brand not in BRAND_NAMES or min(resolution.width, resolution.height) <= 0:
            raise RuntimeError("Invalid camera resolution catalogue entry")
        names = {row["model"], *row["aliases"].split("|")}
        for name in names:
            if name:
                key = (brand, _catalog_model_key(brand, name))
                if resolution not in index.setdefault(key, []):
                    index[key].append(resolution)
    return {key: tuple(entries) for key, entries in index.items()}


def _resolve_camera_resolution(make: str, model: str) -> CameraResolution | None:
    brand = _catalog_brand(make, model)
    if brand is None or not model.strip():
        return None
    key = (brand, _catalog_model_key(brand, model))
    entries = _camera_resolution_catalog().get(key, ())
    return entries[0] if len(entries) == 1 else None


def _catalog_crop_factor(
    make: str, model: str, exif: dict[str, Any], raster_size: tuple[int, int] | None
) -> Decimal | None:
    resolution = _resolve_camera_resolution(make, model)
    if resolution is None or raster_size is None:
        return None
    width, height = raster_size
    if width <= 0 or height <= 0 or exif.get("Orientation", 1) not in range(1, 9):
        return None
    source_size = (resolution.width, resolution.height)
    candidates = (source_size, source_size[::-1])
    # Pillow has already transposed non-RAW images. For a rotated EXIF frame
    # require the swapped catalogue orientation; Orientation=1 also permits a
    # camera that stores portrait pixels directly.
    if exif.get("Orientation", 1) in (5, 6, 7, 8):
        candidates = (source_size[::-1],)
    factors = {
        Decimal(source_width) / Decimal(width)
        for source_width, source_height in candidates
        if source_width > width
        and source_height > height
        and source_width * height == source_height * width
    }
    return next(iter(factors)) if len(factors) == 1 else None


def _dng_crop_factor(
    exif: dict[str, Any], raster_size: tuple[int, int] | None
) -> Decimal | None:
    crops = exif.get("DngUserCrops")
    if not isinstance(crops, tuple) or len(crops) != 1 or raster_size is None:
        return None
    crop = crops[0]
    if not isinstance(crop, DngUserCrop):
        return None
    source_width, source_height = crop.source_size
    crop_width, crop_height = crop.size
    left, top, right, bottom = crop.box
    if not (
        source_width > 0
        and source_height > 0
        and 0 <= left < right <= source_width
        and 0 <= top < bottom <= source_height
    ):
        return None
    orientation = exif.get("Orientation", 1)
    if orientation in (5, 6, 7, 8):
        expected_size = (crop_height, crop_width)
    elif orientation in (1, 2, 3, 4):
        expected_size = crop.size
    else:
        return None
    if raster_size != expected_size or crop_width <= 0 or crop_height <= 0:
        return None
    horizontal = Decimal(source_width) / Decimal(crop_width)
    vertical = Decimal(source_height) / Decimal(crop_height)
    return horizontal if horizontal == vertical and horizontal > 1 else None


def _apply_dng_user_crop(image: Image.Image, exif: dict[str, Any]) -> Image.Image:
    crops = exif.get("DngUserCrops")
    if not isinstance(crops, tuple) or len(crops) != 1:
        return image
    crop = crops[0]
    if not isinstance(crop, DngUserCrop):
        return image
    # Only cut an unrotated full default image area. If libraw has already
    # applied the user crop, its smaller raster is handled by the resolver.
    if image.size == crop.source_size and exif.get("Orientation", 1) == 1:
        return image.crop(crop.box)
    return image


def _format_metadata(
    exif: dict[str, Any], raster_size: tuple[int, int] | None = None
) -> PhotoMetadata:
    make = _clean_text(exif.get("Make", ""))
    model = _clean_text(exif.get("Model", ""))
    brand_key = _camera_brand_key(make, model)
    lens_model = _clean_text(exif.get("LensModel", ""))
    lens_alias = _match_lens_alias(lens_model)

    aperture = _format_aperture(exif.get("FNumber"))
    exposure = _format_exposure(exif.get("ExposureTime"))
    iso = exif.get("PhotographicSensitivity", exif.get("ISOSpeedRatings"))
    # These EXIF tags describe different quantities. Keep their provenance so
    # a physical focal length is never presented as a 35 mm equivalent.
    focal_length_35mm = _format_focal_length(exif.get("FocalLengthIn35mmFilm"))
    focal_length_physical = _format_focal_length(exif.get("FocalLength"))
    if "DngUserCrops" in exif:
        crop_factor = _dng_crop_factor(exif, raster_size)
        crop_basis = "dng_explicit" if crop_factor is not None else None
    else:
        crop_factor = _catalog_crop_factor(make, model, exif, raster_size)
        # The catalogue gives maximum still dimensions. A smaller raster can
        # also result from resize, binning, or a lower-resolution capture mode.
        crop_basis = "catalog_max_still_inferred" if crop_factor is not None else None
    physical_value = _as_decimal(exif.get("FocalLength"))
    focal_length_cropped = (
        _format_focal_length(physical_value * crop_factor)
        if physical_value is not None and crop_factor is not None
        else None
    )

    return PhotoMetadata(
        iso=_clean_text(iso) if iso else "—",
        aperture=aperture or "—",
        focal_length_35mm=focal_length_35mm or "—",
        focal_length_physical=focal_length_physical or "—",
        focal_length_cropped=focal_length_cropped or "—",
        shutter_speed=exposure.upper() if exposure else "—",
        camera_make=make or "UNKNOWN",
        camera_model=_strip_camera_make(make, model, brand_key),
        lens=_format_lens_display(lens_alias)
        if lens_alias
        else _format_lens_name(lens_model),
        lens_badge_key=_lens_badge_key_from_alias(lens_alias)
        or _lens_badge_key(lens_model),
        captured_at=_capture_time_from_exif(exif),
        focal_length_cropped_basis=crop_basis if focal_length_cropped else None,
    )


def _load_font(
    size: int, *, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_BOLD_PATHS if bold else FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "…"
    candidate = text
    while candidate and draw.textlength(candidate + ellipsis, font=font) > max_width:
        candidate = candidate[:-1]
    return candidate.rstrip() + ellipsis


def _centered_text_y(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    center_y: int,
) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    text_height = bounds[3] - bounds[1]
    return round(center_y - text_height / 2 - bounds[1])


def _centered_text_x(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    left: int,
    right: int,
) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    text_width = bounds[2] - bounds[0]
    return round(left + (right - left - text_width) / 2 - bounds[0])


def _lens_badge_key(lens_name: str) -> str | None:
    match_key = _lens_match_key(lens_name)
    tamron_model_codes = (
        "a036",
        "a046",
        "a047",
        "a056",
        "a057",
        "a058",
        "a062",
        "a063",
        "a064",
        "a065",
        "a067",
        "a068",
        "a069",
        "a071",
        "a074",
        "a075",
        "a078",
        "f050",
        "f051",
        "f053",
        "f072",
    )

    if "sigma" in match_key:
        return "sigma"
    if (
        "tamron" in match_key
        or "di iii" in match_key
        or any(term in match_key for term in ("vxd", "rxd", "osd"))
        or any(
            re.search(rf"\b{code}\b", match_key)
            for code in tamron_model_codes
        )
    ):
        return "tamron"
    if re.search(r"\bgm\b", match_key):
        return "gm"
    if re.search(r"\bg\b", match_key):
        return "g"
    return None


def _load_brand_icon(
    icon_dir: Path,
    make: str,
    model: str,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    brand_key = _camera_brand_key(make, model)
    if not brand_key:
        return None

    path = icon_dir / f"{brand_key.replace(' ', '-')}.png"
    if not path.is_file():
        return None

    try:
        with Image.open(path) as source:
            icon = source.convert("RGBA")
            alpha = icon.getchannel("A").point(lambda value: 255 if value > 16 else 0)
            bounds = alpha.getbbox()
            if bounds:
                icon = icon.crop(bounds)
            icon.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            return icon
    except (UnidentifiedImageError, OSError):
        LOGGER.warning("Unable to load brand icon: %s", path)
        return None


def _is_badge_background_pixel(
    red: int, green: int, blue: int, alpha: int
) -> bool:
    if not alpha:
        return False
    is_black = red < 24 and green < 24 and blue < 24
    is_white = red > 238 and green > 238 and blue > 238
    return is_black or is_white


def _make_edge_background_transparent(image: Image.Image) -> Image.Image:
    pixels = image.load()
    width, height = image.size
    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited or not (0 <= x < width and 0 <= y < height):
            continue
        visited.add((x, y))

        red, green, blue, alpha = pixels[x, y]
        if not _is_badge_background_pixel(red, green, blue, alpha):
            continue

        pixels[x, y] = (red, green, blue, 0)
        queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))

    return image


def _load_lens_badge(
    badge_dir: Path,
    badge_key: str | None,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    if not badge_key:
        return None

    path = badge_dir / f"{badge_key}.png"
    if not path.is_file():
        return None

    try:
        with Image.open(path) as source:
            badge = source.convert("RGBA")
            if not source.info.get("transparency"):
                badge = _make_edge_background_transparent(badge)
            alpha = badge.getchannel("A").point(lambda value: 255 if value > 16 else 0)
            bounds = alpha.getbbox()
            if bounds:
                badge = badge.crop(bounds)
            height_factor = LENS_BADGE_HEIGHT_FACTORS.get(badge_key, 1)
            scaled_max_height = max(1, round(max_height * height_factor))
            badge.thumbnail((max_width, scaled_max_height), Image.Resampling.LANCZOS)
            return badge
    except (UnidentifiedImageError, OSError):
        LOGGER.warning("Unable to load lens badge: %s", path)

    return None


def _load_signature(
    configured_path: Path,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    candidates = (
        configured_path,
        configured_path.parent / "signature.example.png",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            with Image.open(path) as source:
                signature = source.convert("RGBA")
                bounds = signature.getbbox()
                if bounds:
                    signature = signature.crop(bounds)
                signature.thumbnail(
                    (max_width, max_height), Image.Resampling.LANCZOS
                )
                return signature
        except (UnidentifiedImageError, OSError):
            LOGGER.warning("Unable to load signature image: %s", path)

    LOGGER.warning("No usable signature image found at %s", configured_path)
    return None


def _metadata_stats(metadata: PhotoMetadata) -> tuple[tuple[str, str], ...]:
    if metadata.focal_length_cropped != "—":
        crop_label = (
            "CROP FOCAL"
            if metadata.focal_length_cropped_basis == "dng_explicit"
            else "CROP EST."
        )
        focal_stat = (crop_label, metadata.focal_length_cropped)
    elif metadata.focal_length_35mm != "—":
        focal_stat = ("35MM EQ", metadata.focal_length_35mm)
    elif metadata.focal_length_physical != "—":
        focal_stat = ("FOCAL", metadata.focal_length_physical)
    else:
        focal_stat = ("FOCAL", metadata.focal_length_physical)
    return (
        ("ISO", metadata.iso),
        ("APERTURE", metadata.aperture),
        ("SHUTTER", metadata.shutter_speed),
        focal_stat,
    )


def _draw_stat_row(
    draw: ImageDraw.ImageDraw,
    stats: tuple[tuple[str, str], ...],
    left: int,
    right: int,
    label_y: int,
    value_y: int,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    value_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    gap: int,
) -> None:
    stat_width = max(1, (right - left) // len(stats))
    for index, (label, value) in enumerate(stats):
        x = left + stat_width * index
        draw.text((x, label_y), label, font=label_font, fill=PANEL_MUTED)
        fitted_value = _fit_text(draw, value, value_font, stat_width - gap)
        draw.text((x, value_y), fitted_value, font=value_font, fill=PANEL_INK)


def _add_portrait_metadata_panel(
    photo: Image.Image,
    metadata: PhotoMetadata,
    signature_path: Path,
) -> Image.Image:
    width, height = photo.size
    panel_height = max(round(PANEL_MIN_HEIGHT * 1.35), round(width * 0.105))
    padding = max(22, round(width * 0.032))
    label_font = _load_font(max(9, round(width * 0.0084)))
    value_font = _load_font(max(17, round(width * 0.0172)), bold=True)
    detail_font = _load_font(max(10, round(width * 0.0102)))
    equipment_font = _load_font(max(12, round(width * 0.0128)), bold=True)
    small_font = _load_font(max(10, round(width * 0.0094)))

    panel = Image.new("RGB", (width, panel_height), PANEL_BACKGROUND)
    draw = ImageDraw.Draw(panel)
    line_width = max(2, round(width * 0.002))
    draw.line((0, 0, width, 0), fill=(226, 222, 214), width=line_width)
    draw.line(
        (padding, 0, padding + round(width * 0.14), 0),
        fill=PANEL_ACCENT,
        width=line_width * 2,
    )

    divider_color = (218, 214, 206)
    left_right = round(width * 0.61)
    divider_x = left_right
    draw.line(
        (
            divider_x,
            round(panel_height * 0.18),
            divider_x,
            round(panel_height * 0.82),
        ),
        fill=divider_color,
        width=max(1, line_width // 2),
    )

    left_section_right = divider_x - round(width * 0.028)
    _draw_stat_row(
        draw,
        _metadata_stats(metadata),
        padding,
        left_section_right,
        round(panel_height * 0.18),
        round(panel_height * 0.42),
        label_font,
        value_font,
        round(width * 0.018),
    )

    lower_y = round(panel_height * 0.78)
    captured_right = left_section_right
    draw.text(
        (padding, lower_y),
        "CAPTURED",
        font=label_font,
        fill=PANEL_ACCENT,
    )
    captured_x = padding + round(width * 0.13)
    captured_width = max(1, captured_right - captured_x)
    captured_text = _fit_text(draw, metadata.captured_at, small_font, captured_width)
    draw.text(
        (captured_x, lower_y),
        captured_text,
        font=small_font,
        fill=PANEL_MUTED,
    )

    right_left = divider_x + round(width * 0.035)
    right_right = width - padding
    badge_left = right_left
    badge_right = round(width * 0.74)
    equipment_left = badge_right + round(width * 0.028)
    equipment_right = right_right
    signature = _load_signature(
        signature_path,
        max_width=max(1, round((equipment_right - equipment_left) * 1.15)),
        max_height=max(1, round(panel_height * 0.48)),
    )
    if signature:
        alpha = signature.getchannel("A").point(lambda value: round(value * 0.22))
        faded_signature = signature.copy()
        faded_signature.putalpha(alpha)
        signature_x = equipment_right - faded_signature.width
        signature_y = round(panel_height * 0.68 - faded_signature.height / 2)
        panel.paste(
            faded_signature,
            (signature_x, signature_y),
            faded_signature,
        )

    badge_area_width = max(1, badge_right - badge_left)
    lens_badge = _load_lens_badge(
        LENS_BADGE_DIR,
        metadata.lens_badge_key,
        max_width=badge_area_width,
        max_height=max(1, round(panel_height * 0.13)),
    )
    brand_icon = _load_brand_icon(
        BRAND_ICON_DIR,
        metadata.camera_make,
        metadata.camera_model,
        max_width=max(1, round(badge_area_width * 0.94)),
        max_height=max(
            1,
            round(panel_height * (0.14 if lens_badge else 0.22)),
        ),
    )
    brand_center_y = round(panel_height * (0.38 if lens_badge else 0.50))
    if brand_icon:
        brand_x = round(badge_left + (badge_area_width - brand_icon.width) / 2)
        brand_y = round(brand_center_y - brand_icon.height / 2)
        panel.paste(brand_icon, (brand_x, brand_y), brand_icon)
    else:
        brand_key = _camera_brand_key(metadata.camera_make, metadata.camera_model)
        brand_text = BRAND_NAMES.get(brand_key, metadata.camera_make).upper()
        brand_text = _fit_text(draw, brand_text, value_font, badge_area_width)
        brand_x = _centered_text_x(
            draw, brand_text, value_font, badge_left, badge_right
        )
        brand_y = _centered_text_y(draw, brand_text, value_font, brand_center_y)
        draw.text((brand_x, brand_y), brand_text, font=value_font, fill=PANEL_INK)
    if lens_badge:
        lens_badge_x = round(badge_left + (badge_area_width - lens_badge.width) / 2)
        lens_badge_y = round(panel_height * 0.62 - lens_badge.height / 2)
        panel.paste(lens_badge, (lens_badge_x, lens_badge_y), lens_badge)

    equipment_width = max(1, equipment_right - equipment_left)
    equipment_camera_y = round(panel_height * 0.30)
    equipment_lens_y = round(panel_height * 0.50)
    camera_text = _fit_text(
        draw, metadata.camera_model, equipment_font, equipment_width
    )
    draw.text(
        (equipment_left, equipment_camera_y),
        camera_text,
        font=equipment_font,
        fill=PANEL_INK,
    )

    lens_text = _fit_text(draw, metadata.lens, equipment_font, equipment_width)
    draw.text(
        (equipment_left, equipment_lens_y),
        lens_text,
        font=equipment_font,
        fill=PANEL_INK,
    )

    result = Image.new("RGB", (width, height + panel_height), PANEL_BACKGROUND)
    result.paste(photo, (0, 0))
    result.paste(panel, (0, height))
    return result


def _add_metadata_panel(
    image: Image.Image,
    metadata: PhotoMetadata,
    signature_path: Path,
) -> Image.Image:
    photo = image.convert("RGB")
    width, height = photo.size
    if height > width:
        return _add_portrait_metadata_panel(photo, metadata, signature_path)

    panel_height = max(PANEL_MIN_HEIGHT, round(width * PANEL_HEIGHT_RATIO))
    padding = max(20, round(width * 0.026))
    label_font = _load_font(max(9, round(width * 0.0088)))
    value_font = _load_font(max(15, round(width * 0.017)), bold=True)
    detail_font = _load_font(max(11, round(width * 0.011)))
    equipment_font = _load_font(max(13, round(width * 0.0135)), bold=True)

    panel = Image.new("RGB", (width, panel_height), PANEL_BACKGROUND)
    draw = ImageDraw.Draw(panel)
    line_width = max(2, round(width * 0.002))
    draw.line((0, 0, width, 0), fill=(226, 222, 214), width=line_width)
    draw.line(
        (padding, 0, padding + round(width * 0.07), 0),
        fill=PANEL_ACCENT,
        width=line_width * 2,
    )

    stats_right = round(width * 0.55)
    signature_left = round(width * 0.59)
    signature_badge_left = round(width * 0.70)
    signature_right = round(width * 0.78)
    equipment_left = round(width * 0.80)
    divider_top = round(panel_height * 0.16)
    divider_bottom = round(panel_height * 0.84)
    divider_color = (218, 214, 206)
    draw.line(
        (round(width * 0.575), divider_top, round(width * 0.575), divider_bottom),
        fill=divider_color,
        width=max(1, line_width // 2),
    )
    draw.line(
        (signature_right, divider_top, signature_right, divider_bottom),
        fill=divider_color,
        width=max(1, line_width // 2),
    )

    _draw_stat_row(
        draw,
        _metadata_stats(metadata),
        padding,
        stats_right,
        round(panel_height * 0.22),
        round(panel_height * 0.42),
        label_font,
        value_font,
        round(width * 0.012),
    )

    captured_y = round(panel_height * 0.76)
    draw.text(
        (padding, captured_y),
        "CAPTURED",
        font=label_font,
        fill=PANEL_ACCENT,
    )
    captured_x = padding + round(width * 0.072)
    captured_width = max(1, stats_right - captured_x - padding)
    captured_text = _fit_text(
        draw, metadata.captured_at, label_font, captured_width
    )
    draw.text(
        (captured_x, captured_y),
        captured_text,
        font=label_font,
        fill=PANEL_MUTED,
    )

    draw.text(
        (signature_left, round(panel_height * 0.18)),
        "SHOT BY",
        font=label_font,
        fill=PANEL_MUTED,
    )
    signature_gap = max(8, round(width * 0.008))
    signature_width = max(1, signature_badge_left - signature_left - signature_gap)
    signature = _load_signature(
        signature_path,
        max_width=signature_width,
        max_height=max(1, round(panel_height * 0.38)),
    )
    if signature:
        signature_y = round(panel_height * 0.41)
        panel.paste(signature, (signature_left, signature_y), signature)
    else:
        draw.text(
            (signature_left, round(panel_height * 0.48)),
            "SIGNATURE",
            font=detail_font,
            fill=PANEL_INK,
        )

    badge_area_left = signature_badge_left
    badge_area_right = signature_right - signature_gap
    badge_area_width = max(1, badge_area_right - badge_area_left)
    lens_badge = _load_lens_badge(
        LENS_BADGE_DIR,
        metadata.lens_badge_key,
        max_width=badge_area_width,
        max_height=max(1, round(panel_height * 0.24)),
    )
    brand_icon = _load_brand_icon(
        BRAND_ICON_DIR,
        metadata.camera_make,
        metadata.camera_model,
        max_width=max(1, round(badge_area_width * 0.94)),
        max_height=max(
            1,
            round(panel_height * (0.22 if lens_badge else 0.30)),
        ),
    )
    brand_center_y = round(panel_height * (0.32 if lens_badge else 0.54))
    if brand_icon:
        brand_x = round(badge_area_left + (badge_area_width - brand_icon.width) / 2)
        brand_y = round(brand_center_y - brand_icon.height / 2)
        panel.paste(brand_icon, (brand_x, brand_y), brand_icon)
    else:
        brand_key = _camera_brand_key(metadata.camera_make, metadata.camera_model)
        brand_text = BRAND_NAMES.get(brand_key, metadata.camera_make).upper()
        brand_text = _fit_text(draw, brand_text, value_font, badge_area_width)
        brand_x = _centered_text_x(
            draw, brand_text, value_font, badge_area_left, badge_area_right
        )
        brand_y = _centered_text_y(draw, brand_text, value_font, brand_center_y)
        draw.text((brand_x, brand_y), brand_text, font=value_font, fill=PANEL_INK)
    if lens_badge:
        lens_badge_x = round(
            badge_area_left + (badge_area_width - lens_badge.width) / 2
        )
        lens_badge_y = round(panel_height * 0.65 - lens_badge.height / 2)
        panel.paste(lens_badge, (lens_badge_x, lens_badge_y), lens_badge)

    equipment_right = width - padding
    equipment_width = max(1, equipment_right - equipment_left)
    model_width = max(1, equipment_width)
    camera_text = _fit_text(
        draw, metadata.camera_model, equipment_font, model_width
    )
    camera_y = _centered_text_y(
        draw, camera_text, equipment_font, round(panel_height * 0.34)
    )
    draw.text(
        (equipment_left, camera_y),
        camera_text,
        font=equipment_font,
        fill=PANEL_INK,
    )

    lens_text = _fit_text(draw, metadata.lens, equipment_font, equipment_width)
    lens_y = _centered_text_y(
        draw, lens_text, equipment_font, round(panel_height * 0.65)
    )
    draw.text(
        (equipment_left, lens_y),
        lens_text,
        font=equipment_font,
        fill=PANEL_INK,
    )

    result = Image.new("RGB", (width, height + panel_height), PANEL_BACKGROUND)
    result.paste(photo, (0, 0))
    result.paste(panel, (0, height))
    return result


def _source_extension(image_source: bytes | Path, original_name: str | None) -> str:
    source_name = original_name
    if not source_name and isinstance(image_source, Path):
        source_name = image_source.name
    return Path(source_name or "").suffix.lower().lstrip(".")


def _is_raw_source(image_source: bytes | Path, original_name: str | None) -> bool:
    return _source_extension(image_source, original_name) in RAW_FILE_EXTENSIONS


def _raw_input(image_source: bytes | Path) -> str | BytesIO:
    return str(image_source) if isinstance(image_source, Path) else BytesIO(image_source)


def _source_dimensions_without_decode(
    image_source: bytes | Path,
    original_name: str | None,
) -> tuple[int, int]:
    """Read image dimensions without loading the full raster into memory."""

    if _is_raw_source(image_source, original_name):
        try:
            with rawpy.imread(_raw_input(image_source)) as raw:
                sizes = raw.sizes
                return int(sizes.width), int(sizes.height)
        except (rawpy.LibRawError, OSError, ValueError, AttributeError) as exc:
            raise UnidentifiedImageError(
                "Unsupported or invalid RAW image"
            ) from exc

    source_input = (
        image_source if isinstance(image_source, Path) else BytesIO(image_source)
    )
    bomb_warning = getattr(Image, "DecompressionBombWarning", None)
    try:
        with warnings.catch_warnings():
            if isinstance(bomb_warning, type) and issubclass(
                bomb_warning, Warning
            ):
                # Pillow normally emits this warning while opening the header.
                # Turn it into the same handled error as DecompressionBombError.
                warnings.simplefilter("error", bomb_warning)
            with Image.open(source_input) as source:
                return int(source.size[0]), int(source.size[1])
    except _PIL_DECOMPRESSION_BOMB_EXCEPTIONS as exc:
        raise ImageTooLargeError(
            "Pillow rejected the image dimensions as a decompression bomb"
        ) from exc


def _validate_image_dimensions(
    image_source: bytes | Path,
    original_name: str | None,
    max_image_pixels: int,
) -> None:
    """Reject oversized images before any complete pixel decode takes place."""

    if max_image_pixels <= 0:
        raise ValueError("MAX_IMAGE_PIXELS must be greater than zero")

    width, height = _source_dimensions_without_decode(image_source, original_name)
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")

    pixel_count = width * height
    if pixel_count > max_image_pixels:
        raise ImageTooLargeError(
            f"Image has {pixel_count} pixels; configured limit is "
            f"{max_image_pixels}"
        )


def _decode_raw_image(image_source: bytes | Path) -> Image.Image:
    try:
        with rawpy.imread(_raw_input(image_source)) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=False,
                auto_bright_thr=0.01,
                bright=RAW_BRIGHTNESS,
                exp_shift=RAW_EXPOSURE_SHIFT,
                exp_preserve_highlights=0.75,
                highlight_mode=rawpy.HighlightMode.Blend,
                output_bps=8,
            )
    except (rawpy.LibRawError, OSError, ValueError) as exc:
        raise UnidentifiedImageError("Unsupported or invalid RAW image") from exc

    return Image.fromarray(rgb, "RGB")


def process_image(
    image_source: bytes | Path,
    signature_path: Path,
    jpeg_quality: int,
    original_name: str | None = None,
) -> tuple[BytesIO, bool]:
    if _is_raw_source(image_source, original_name):
        exif_values = _read_raw_exif(image_source, original_name)
        image = _decode_raw_image(image_source)
        image = _apply_dng_user_crop(image, exif_values)
        exif_bytes = b""
        metadata = _format_metadata(exif_values, image.size)
        result = _add_metadata_panel(image, metadata, signature_path)
    else:
        source_input = (
            image_source if isinstance(image_source, Path) else BytesIO(image_source)
        )
        with Image.open(source_input) as source:
            source.load()
            exif_values = _read_exif(source)
            image = ImageOps.exif_transpose(source)
            exif_bytes = image.getexif().tobytes()
            metadata = _format_metadata(exif_values, image.size)
            result = _add_metadata_panel(image, metadata, signature_path)

    output = BytesIO()
    save_options: dict[str, Any] = {
        "format": "JPEG",
        "quality": jpeg_quality,
        "subsampling": 0,
        "optimize": True,
    }
    if exif_bytes:
        save_options["exif"] = exif_bytes
    result.save(output, **save_options)
    output.seek(0)
    return output, bool(exif_values)


def _output_filename(original_name: str | None) -> str:
    stem = Path(original_name or "photo").stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "photo"
    return f"{safe_stem}_watermarked.jpg"


def _document_extension_filter(extensions: tuple[str, ...]) -> Any:
    extension_filter = filters.Document.FileExtension(extensions[0])
    for extension in extensions[1:]:
        extension_filter |= filters.Document.FileExtension(extension)
    return extension_filter


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text(
            "Send an image as a file. I will add its EXIF details and watermark."
        )


async def remind_file_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text(
            "Please send the image as a file to preserve its quality and EXIF data."
        )


async def handle_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    document = message.document if message else None
    if not message or not document:
        return

    settings: Settings = context.application.bot_data["settings"]
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if document.file_size and document.file_size > max_bytes:
        await message.reply_text(
            f"The file exceeds the {settings.max_file_size_mb} MB limit."
        )
        return

    status = await message.reply_text("Uploading image....")
    await context.bot.send_chat_action(
        chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT
    )

    try:
        telegram_file = await document.get_file(
            read_timeout=settings.file_transfer_timeout_seconds
        )
        if settings.local_mode:
            if not telegram_file.file_path:
                raise OSError("Local Bot API returned no file path")
            image_source: bytes | Path = Path(telegram_file.file_path)
            if not image_source.is_file():
                raise OSError(
                    f"Shared Telegram file is unavailable: {image_source}"
                )
        else:
            image_source = bytes(await telegram_file.download_as_bytearray())

        await status.edit_text("Processing image...")
        await asyncio.to_thread(
            _validate_image_dimensions,
            image_source,
            document.file_name,
            settings.max_image_pixels,
        )
        output, has_exif = await asyncio.to_thread(
            process_image,
            image_source,
            settings.signature_image_path,
            settings.jpeg_quality,
            document.file_name,
        )
        caption = (
            "Watermark and EXIF details added."
            if has_exif
            else "Watermark added. No EXIF data was found."
        )
        await message.reply_document(
            document=InputFile(output, filename=_output_filename(document.file_name)),
            caption=caption,
            read_timeout=settings.file_transfer_timeout_seconds,
            write_timeout=settings.file_transfer_timeout_seconds,
        )
        await status.edit_text("Done!")
    except ImageTooLargeError as exc:
        LOGGER.warning("Image exceeds the configured pixel limit: %s", exc)
        await status.edit_text(IMAGE_TOO_LARGE_MESSAGE)
    except _PIL_DECOMPRESSION_BOMB_EXCEPTIONS as exc:
        LOGGER.warning("Pillow rejected an image as a decompression bomb: %s", exc)
        await status.edit_text(IMAGE_TOO_LARGE_MESSAGE)
    except (UnidentifiedImageError, OSError, ValueError):
        LOGGER.warning("Unsupported or invalid image received", exc_info=True)
        await status.edit_text("This image format is unsupported or the file is invalid.")
    except Exception:
        LOGGER.exception("Failed to process image")
        await status.edit_text("Image processing failed. Please try another file.")


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_environment()
    application = (
        Application.builder()
        .token(settings.bot_token)
        .base_url(settings.bot_api_base_url)
        .local_mode(settings.local_mode)
        .build()
    )
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", start))
    extra_image_extensions = ("heic", "heif", *sorted(RAW_FILE_EXTENSIONS))
    image_document_filter = (
        filters.Document.IMAGE
        | _document_extension_filter(extra_image_extensions)
    )
    application.add_handler(MessageHandler(image_document_filter, handle_image))
    application.add_handler(MessageHandler(filters.PHOTO, remind_file_upload))
    LOGGER.info("Starting Telegram bot")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
