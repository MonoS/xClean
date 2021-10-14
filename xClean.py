from vapoursynth import core
import vapoursynth as vs
import math
from typing import Optional

"""
xClean 3-pass denoiser
beta 3 (2021-10-13) by Etienne Charland
Supported formats: YUV, GRAY
Requires: rgsf, rgvs, fmtc, mv, mvsf, tmedian, knlm, bm3dcuda_rtc, bm3dcpu

xClean runs MVTools -> BM3D -> KNLMeans in that order, passing the output of each pass as the ref of the next denoiser.

The objective is to remove noise while preserving as much details as possible. Removing noise is easy -- just blur out everything.
The hard work is in preserving the details in a way that feels natural.

I designed it for raw camera footage to remove noise in dark areas while preserving the fine details. It works for most types of content.

+++ Short Doc (TL;DR) +++

Default settings provide the best quality in most cases. Simply use
xClean(sharp=..., outbits=...)
For top quality, you can add d=3, and/or run vcm.Median(maxgrid=9) before xClean
For better performance, set m1=0 or m2=0
If only darker areas contain noise, set strength=-50.

For 720p WebCam, optimal settings are: sharp=9.5, m1=.65, h=2.8
For 288p anime, optimal settings are: sharp=9.5, m1=.7, rn=0, optional depth=1
For 5K GoPro (with in-camera sharpening at Low), optimal settings are: sharp=7.7, m1=.6 (or .5 for performance), optional strength=-50


+++ Description +++

KNLMeans does a good job at denoising but can soften the image, lose details and give an artificial plastic look. I found that on any given source
(tested 5K GoPro footage and noisy WebCam), denoising with less than h=1.4 looks too noisy, and anything above it blurs out the details. 
I thus run it at 1.4 strength. KNLMeans also keeps a lot of data from the clip passed as rclip, so doing a good prefilter highly impacts the output.

Similarly, BM3D performs best with sigma=9. A lower value doesn't remove enough noise, and a higher value only makes the edges sharper.

xClean is essentially KNLMeans with advanced pre-filtering and with post-processing to renoise & sharpen to make the image look more natural.

One strange aspect of xClean is that denoising is automatic and there's very little room to configure denoising strength other than reducing the overall effect.
It runs with BM3D sigma=9 and KNL h=1.4, and generally you shouldn't change that. One setting that can allow increasing denoising (and performance)
is downscaling MVTools pass.

According to my tests, water & cliff 5K video with little noise preserves the details very well while removing subtle grain, and with same settings,
very noisy 720p WebCam footage has HUGE noise reduction while preserving a surprising amount of natural details.

The default settings are very tolerant to various types of clips.


+++ Denoising Methods Overview +++

To provide the best output, processing is done in 3 passes, passing the output of one pass as the ref clip of the 2nd pass. Each denoiser has its strengths and weaknesses.

Pass 1: MVTools (m1)
Strength: Removes a lot of noise, good at removing temporal noise.
Weakness: Can remove too much, especially with delicate textures like water.
Ref: Impacts vectors analysis but low impact on outcome

Pass 2: BM3D (m2)
Strength: Good at preserving fine details!
Weakness: Doesn't remove much grain. Poor temporal stability.
Ref: Moderate impact on outcome. A blurry ref will remove more grain while BM3D puts back a lot of details.
radius=1 provides nearly no benefit at huge performance cost since MVTools already does temporal analysis

Pass 3: KNLMeansCL (m3)
Strength: Best general-purpose denoiser
Weakness: Can blur out details and give an artificial plastic effect
Ref: Highly impacts the outcome. All prefilters benefit from running KNLMeans over it.

MVTools + BM3D (m1+m2)
Strength: Keeps a lot of details with good denoising of fine details as well as larger grain. Single frames can look great.
Weakness: Single frames look fine but video noise looks unstable in motion.

MVTools + KNLMeans (m1+m3)
Strength: KNLMeans with extra denoising. Works best in most circumstances.
Weakness: Delicate textures like water or fog will suffer.

MVTools + BM3D + KNLMeans (m1+m2+m3)
Strength: Like MvTools+KNLMeans but preserves details with delicate textures. Works best for any kind of content tested.
Weakness: Performance and memory usage.


+++ Denoising Pass Configuration  (m1=.6, m2=3, m3=3) +++

Each pass (method) can be configured with m1 (MVTools), m2 (BM3D) and m3 (KNLMeansCL) parameters to run at desired bitdepth.
This means you can fine-tune for quality vs performance.

0 = Disabled, 1 = 8-bit, 2 = 16-bit, 3 = 16-bit YUV444, 4 = 32-bit YUV444

Note: BM3D always processes in 32-bit, KNLMeansCL always processes in 16-bit+, and post-processing always processes at least in 16-bit, so certain
values such as m2=1, m3=1 will behave the same as m2=2, m3=2. Setting m2=3 will only affect BM3D post-processing (YUV444P16 instead of YUV420P16)

MVTools pass (m1) can also be downscaled for performance gain, and it can even improve quality! Values between .5 and .8 generally work best.
Resizing by a factor of .6 provides the best quality in my tests, and .5 works best if you want that extra performance.

Optional resize factor is set after the dot:
m1 = .6 or 1.6 processes in 8-bit at 60% of the size. m1 = 2.6 processes in 16-bit at 60% of the size.

For better performance, you can disable m1 for videos with delicate textures and low noise, or disable m2 for videos with no delicate textures.
You can also simply resize MVTools pass smaller (.5 or .4) which will produce a bit more blur. If m1=1 (no downsize), you can reduce sharp from 11 to 10.


+++ Renoise and Sharpen  (rn=14, sharp=9.5) +++

The idea comes from mClean by Burfadel (https://forum.doom9.org/showthread.php?t=174804) and the algorithm was changed by someone else while porting 
to VapourSynth, producing completely different results -- original Avisynth version blurs a lot more, VapourSynth version keeps a lot more details.

It may sound counter-productive at first, but the idea is to combat the flat or plastic effect of denoising by re-introducing part of the removed noise.
The noise is processed and stabilized before re-inserting so that it's less distracting.
Renoise also helps reduce large-radius grain; but should be disabled for anime (rn=0).

Using the same analysis data, it's also sharpening to compensate for denoising blur.
Sharpening must be between 0 and 20. Actual sharpening calculation is scaled based on resolution.


+++ Strength / Dynamic Denoiser Strength  (strength=20) +++

A value of 20 will denoise normally.
A value between 1 and 19 will reduce the denoising effect by partially merging back with the original clip.

A value between 0 and -200 will activate Dynamic Denoiser Strength, useful when bright colors require little or no denoising and dark colors contain more noise.
It applies a gradual mask based on luma. Specifying a value of -50 means that out of 255 (or 219 tv range), the 50 blackest values have full-reduction 
and the 50 whitest values are merged at a minimal strength of 50/255 = 20%.


+++ Depth  (depth=0) +++
This applies a modified warp sharpening on the image that may be useful for certain things, and can improve the perception of image depth.
Settings range up from 0 to 5. This function will distort the image, for animation a setting of 1 or 2 can be beneficial to improve lines.

+++ Deband  (deband=False) +++
This will perceptibly improve the quality of the image by reducing banding effect and adding a small amount of temporally stabilised grain
to both luma and chroma. The settings are not adjustable as the default settings are suitable for most cases without having a large effect
on compressibility. 0 = disabled, 1 = deband

+++ Output  (outbits, dmode=0) +++
Specifies the output bitdepth. If not specified it will be converted back to the bitdepth of the source clip using dithering method specified by dmode.
You can set dmode=3 if you won't be doing any further processing.

+++ Chroma  (chroma=False) +++
True to process both Luma and Chroma planes, False to process only Luma.

+++ Anime +++
For anime, set rn=0. Optionally, you can set depth to 1 or 2 to thicken the lines.

+++ Advanced Settings +++
gpuid = 0: The GPU id to use for KNLMeans and BM3D, or -1 to use CPU.
gpucuda = 0: The GPU id to use for BM3D, or -1 to use CPU.
d = 2: KNLMeans temporal radius, can be set to 3 for small quality improvement.
h = 1.4: KNLMeans strength, can increase slightly if the output is still too noisy.
block_step = 5, bm_range = 15, ps_range = 7: BM3D parameters for performance vs quality.

Normally you shouldn't have to touch these
rgmode = 18: RemoveGrain mode used during post-processing. Setting this to 0 disables post-processing, useful to compare raw denoising.
thsad = 400: Threshold used for MVTools analysis.
a = 2: KNLMeans spacial radius.
sigma = 9: BM3D strength.
"""

def xClean(clip: vs.VideoNode, chroma: bool = True, sharp: float = 9.5, rn: float = 14, deband: bool = False, depth: int = 0, strength: int = 20, m1: float = .6, m2: int = 3, m3: int = 3, outbits: Optional[int] = None,
        dmode: int = 0, rgmode: int = 18, thsad: int = 400, d: int = 2, a: int = 2, h: float = 1.4, gpuid: int = 0, gpucuda: Optional[int] = None, sigma: float = 9, block_step: int = 5, bm_range: int = 15, ps_range: int = 7) -> vs.VideoNode:
    if not clip.format.color_family in [vs.YUV, vs.GRAY]:
        raise TypeError("xClean: Only YUV or GRAY clips are supported")

    defH = max(clip.height, clip.width // 4 * 3) # Resolution calculation for auto blksize settings
    if sharp < 0 or sharp > 20:
        raise ValueError("xClean: sharp must be between 0 and 20")
    if rn < 0 or rn > 20:
        raise ValueError("xClean: rn (renoise strength) must be between 0 and 20")
    if depth < 0 or depth > 5:
        raise ValueError("xClean: depth must be between 0 and 5")
    if strength < -200 or strength > 20:
        raise ValueError("xClean: strength must be between -200 and 20")
    if m1 < 0 or m1 >= 5:
        raise ValueError(r"xClean: m1 (MVTools pass) can be 0 (disabled), 1 (8-bit), 2 (16-bit), 3 (16-bit YUV444) or 4 (32-bit YUV444), plus an optional downscale ratio as decimal (eg: 2.6 resizes to 60% in 16-bit)")
    if m2 < 0 or m2 > 4:
        raise ValueError("xClean: m2 (BM3D pass) can be 0 (disabled), 1 (8-bit), 2 (16-bit), 3 (16-bit YUV444) or 4 (32-bit YUV444)")
    if m3 < 0 or m3 > 4:
        raise ValueError("xClean: m3 (KNLMeansCL pass) can be 0 (disabled), 1 (8-bit), 2 (16-bit), 3 (16-bit YUV444) or 4 (32-bit YUV444)")
    if m1 == 0 and m2 == 0 and m3 == 0:
        raise ValueError("xClean: At least one pass must be enabled")

    gpucuda = gpucuda if gpucuda != None else gpuid
    bd = clip.format.bits_per_sample
    samp = ClipSampling(clip)
    is444 = samp == "444"
    isGray = samp == "GRAY"
    outbits = outbits or bd
    if not outbits in [8, 10, 12, 14, 16, 32]:
        raise ValueError("xClean: outbits must be 8, 10, 12, 14, 16 or 32")

    c = c16 = c_444 = c16_444 = c32_444 = clip
    if bd != 8:
        c = c.fmtc.bitdepth(bits=8, dmode=0)
    if bd != 16:
        c16 = c.fmtc.bitdepth(bits=16, dmode=0)
    if bd != 8 or not is444:
        c_444 = c.resize.Bicubic(format=vs.YUV444P8, dither_type="ordered") if not isGray else c
    if bd != 16 or not is444:
        c16_444 = c.resize.Bicubic(format=vs.YUV444P16, dither_type="ordered") if not isGray else c16
    if bd != 32 or not is444:
        c32_444 = c.resize.Bicubic(format=vs.YUV444PS) if not isGray else c.fmtc.bitdepth(bits=32, dmode=1)
    output = None

    # Apply MVTools
    if m1 > 0:
        m1r = m1 % 1 # Decimal point is resize factor
        m1 = int(m1)
        c1 = c32_444 if m1 == 4 else c16_444 if m1 == 3 else c16 if m1 == 2 else c
        if m1r > 0:
            c1 = c1.resize.Bicubic((c.width * m1r)//2*2, (c.height * m1r)//2*2)
        output = MvTools(c1, chroma, defH, thsad) if m1 < 4 else SpotLess(c1, chroma=chroma, radt=3)
        if c1.format.bits_per_sample < 16:
            c1 = c1.fmtc.bitdepth(bits=16, dmode=1)
        # Adjust sharp based on h parameter.
        # m1=1: sharp=9.6,  m1=.5: sharp=10.3, m1=.3: sharp=10.6
        sharp1 = max(0, min(20, sharp + (1 - m1r) * 1.35))
        output = PostProcessing(output, c1, defH, strength, sharp1, rn, rgmode, 0)
        if m1r > 0:
            output = output.resize.Bicubic(c.width, c.height)

    # Apply BM3D
    if m2 > 0:
        m2o = max(2, max(m2, m3))
        ref = output
        c2 = c32_444 if m2o==4 else c16_444 if m2o==3 else c16
        output = BM3D(c2, sigma, gpucuda, chroma, ref, m2o, block_step, bm_range, ps_range)
        output = PostProcessing(output, c2, defH, strength, sharp, rn, rgmode, 1)
    
    # Apply KNLMeans
    if m3 > 0:
        m3 = min(2, m3) # KNL internally computes in 16-bit
        ref = ConvertToM(output, clip, m3) if output else None
        c3 = c32_444 if m3==4 else c16_444 if m3==3 else c16
        output = KnlMeans(c3, d, a, h, gpuid, chroma, ref)
        # Adjust sharp based on h parameter.
        # h=0: sharp=10.3, h=1.4: sharp=10.8, h=2.8: sharp=11.3
        sharp3 = max(0, min(20, sharp - .5 + (h/2.8)))
        output = PostProcessing(output, c3, defH, strength, sharp3, rn, rgmode, 2)

    # Add Depth (thicken lines for anime)
    if depth:
        depth2 = -depth*3
        depth = depth*2
        output = core.std.MergeDiff(output, core.std.MakeDiff(output.warp.AWarpSharp2(128, 3, 1, depth2, 1), output.warp.AWarpSharp2(128, 2, 1, depth, 1)))
    
    # Apply deband
    if deband:
        if output.format.bits_per_sample == 32:
            output = output.fmtc.bitdepth(bits=16, dmode=0)
        output = output.f3kdb.Deband(range=16, preset="high" if chroma else "luma", grainy=defH/15, grainc=defH/16 if chroma else 0)

    # Convert to desired bitrate
    outsamp = ClipSampling(output)
    if outsamp != samp:
        output = output.fmtc.resample(kernel="bicubic", css=samp)
    if output.format.bits_per_sample != outbits:
        output = output.fmtc.bitdepth(bits=outbits, dmode=dmode)
    
    return output


def PostProcessing(clean: vs.VideoNode, c: vs.VideoNode, defH: int, strength: int, sharp: float, rn: float, rgmode: int, method: int) -> vs.VideoNode:
    # Only apply renoise & sharpen to MVTools method
    if rgmode == 0:
        sharp = 0
        rn = 0
        rgmode = 0

    # Run at least in 16-bit
    if clean.format.bits_per_sample < 16:
        clean = clean.fmtc.bitdepth(bits=16, dmode=1)
    bd = clean.format.bits_per_sample
    
    # Separate luma and chroma
    filt = clean
    clean = core.std.ShufflePlanes(clean, [0], vs.GRAY) if clean.format.num_planes != 1 else clean
    cy = core.std.ShufflePlanes(c, [0], vs.GRAY)

    # Spatial luma denoising
    RG = core.rgsf.RemoveGrain if bd == 32 else core.rgvs.RemoveGrain
    clean2 = RG(clean, rgmode) if rgmode > 0 else clean

    # Apply dynamic noise reduction strength based on Luma
    if strength <= 0:
        # Slightly widen the exclusion mask to preserve details and edges
        cleanm = cy.std.Maximum()
        if defH > 500:
            cleanm = cleanm.std.Maximum()
        if defH > 1200:
            cleanm = cleanm.std.Maximum()

        # Adjust mask levels
        fullRange = GetColorRange(c)
        cleanm = cleanm.std.Levels((0 if fullRange else 16) - strength, 255 if fullRange else 235, 0.85, 0, 255+strength)

        # Merge based on luma mask
        clean = core.std.MaskedMerge(clean, cy, cleanm)
        clean2 = core.std.MaskedMerge(clean2, cy, cleanm)
        filt = core.std.MaskedMerge(filt, c, cleanm)
    elif strength < 20:
        # Reduce strength by partially merging back with original
        clean = core.std.Merge(cy, clean, 0.2+0.04*strength)
        clean2 = core.std.Merge(cy, clean2, 0.2+0.04*strength)
        filt = core.std.Merge(c, filt, 0.2+0.04*strength)

    i = 0.00392 if bd == 32 else 1 << (bd - 8)
    peak = 1.0 if bd == 32 else (1 << bd) - 1

    # Unsharp filter for spatial detail enhancement
    if sharp:
        RE = core.rgsf.Repair if bd == 32 else core.rgvs.Repair
        mult = .69 if method == 2 else .14 if method == 1 else 1
        sharp = min(50, (15 + defH * sharp * 0.0007) * mult)
        clsharp = core.std.MakeDiff(clean, Sharpen(clean2, amountH=-0.08-0.03*sharp))
        clsharp = core.std.MergeDiff(clean2, RE(clsharp.tmedian.TemporalMedian(), clsharp, 12))
    
    # If selected, combining ReNoise
    noise_diff = core.std.MakeDiff(clean2, cy)
    if rn:
        expr = "x {a} < 0 x {b} > {p} 0 x {c} - {p} {a} {d} - / * - ? ?".format(a=32*i, b=45*i, c=35*i, d=65*i, p=peak)
        clean1 = core.std.Merge(clean2, core.std.MergeDiff(clean2, Tweak(noise_diff.tmedian.TemporalMedian(), cont=1.008+0.00016*rn)), 0.3+rn*0.035)
        clean2 = core.std.MaskedMerge(clean2, clean1, core.std.Expr([core.std.Expr([clean, clean.std.Invert()], 'x y min')], [expr]))

    # Combining spatial detail enhancement with spatial noise reduction using prepared mask
    noise_diff = noise_diff.std.Binarize().std.Invert()
    if rgmode > 0:
        clean2 = core.std.MaskedMerge(clean2, clsharp if sharp else clean, core.std.Expr([noise_diff, clean.std.Sobel()], 'x y max'))

    # Combining result of luma and chroma cleaning
    return core.std.ShufflePlanes([clean2, filt], [0, 1, 2], vs.YUV) if c.format.color_family == vs.YUV else clean2


# mClean denoising method
def MvTools(c: vs.VideoNode, chroma: bool, defH: int, thSAD: int) -> vs.VideoNode:
    bd = c.format.bits_per_sample
    icalc = bd < 32
    cy = core.std.ShufflePlanes(c, [0], vs.GRAY)
    S = core.mv.Super if icalc else core.mvsf.Super
    A = core.mv.Analyse if icalc else core.mvsf.Analyse
    R = core.mv.Recalculate if icalc else core.mvsf.Recalculate

    sc = 8 if defH > 2880 else 4 if defH > 1440 else 2 if defH > 720 else 1
    bs = 16 if defH / sc > 360 else 8
    ov = 6 if bs > 12 else 2
    pel = 1 if defH > 720 else 2
    lampa = 777 * (bs ** 2) // 64
    truemotion = False if defH > 720 else True

    ref = c.std.Convolution(matrix=[1, 2, 1, 2, 4, 2, 1, 2, 1])
    super1 = S(ref if chroma else core.std.ShufflePlanes(ref, [0], vs.GRAY), hpad=bs, vpad=bs, pel=pel, rfilter=4, sharp=1)
    super2 = S(c if chroma else cy, hpad=bs, vpad=bs, pel=pel, rfilter=1, levels=1)
    analyse_args = { 'blksize': bs, 'overlap': ov, 'search': 5, 'truemotion': truemotion }
    recalculate_args = { 'blksize': bs, 'overlap': ov, 'search': 5, 'truemotion': truemotion, 'thsad': 180, 'lambda': lampa }

    # Analysis
    bvec4 = R(super1, A(super1, isb=True,  delta=4, **analyse_args), **recalculate_args) if not icalc else None
    bvec3 = R(super1, A(super1, isb=True,  delta=3, **analyse_args), **recalculate_args)
    bvec2 = R(super1, A(super1, isb=True,  delta=2, badsad=1100, lsad=1120, **analyse_args), **recalculate_args)
    bvec1 = R(super1, A(super1, isb=True,  delta=1, badsad=1500, lsad=980, badrange=27, **analyse_args), **recalculate_args)
    fvec1 = R(super1, A(super1, isb=False, delta=1, badsad=1500, lsad=980, badrange=27, **analyse_args), **recalculate_args)
    fvec2 = R(super1, A(super1, isb=False, delta=2, badsad=1100, lsad=1120, **analyse_args), **recalculate_args)
    fvec3 = R(super1, A(super1, isb=False, delta=3, **analyse_args), **recalculate_args)
    fvec4 = R(super1, A(super1, isb=False, delta=4, **analyse_args), **recalculate_args) if not icalc else None

    # Applying cleaning
    if icalc:
        clean = core.mv.Degrain3(c if chroma else cy, super2, bvec1, fvec1, bvec2, fvec2, bvec3, fvec3, thsad=thSAD)
    else:
        clean = core.mvsf.Degrain4(c if chroma else cy, super2, bvec1, fvec1, bvec2, fvec2, bvec3, fvec3, bvec4, fvec4, thsad=thSAD)

    if bd < 16:
        clean = clean.fmtc.bitdepth(bits=16, dmode=1)
        c = c.fmtc.bitdepth(bits=16, dmode=1)

    if c.format.color_family == vs.YUV:
        uv = core.std.MergeDiff(clean, core.tmedian.TemporalMedian(core.std.MakeDiff(c, clean, [1, 2]), 1, [1, 2]), [1, 2]) if chroma else c
        clean = core.std.ShufflePlanes(clips=[clean, uv], planes=[0, 1, 2], colorfamily=vs.YUV)
    return clean


# SpotLess denoising method (m1=4) EXPERIMENTAL
def SpotLess(c: vs.VideoNode, radt: int = 1, thsad: int = 10000, thsad2: Optional[int] = None, pel: int = 2, chroma: bool = True, blksize: int = 8, overlap: Optional[int] = None, truemotion: bool = True, pglobal: bool = True, blur: float = 0.0, ref: Optional[vs.VideoNode] = None) -> vs.VideoNode:
    if radt < 1 or radt > 3:
        raise ValueError("Spotless: radt must be between 1 and 3")
    if not pel in [1, 2, 4]:
        raise ValueError("Spotless: pel must be 1, 2 or 4")

    thsad2 = thsad2 or thsad
    icalc = c.format.bits_per_sample < 32
    S = core.mv.Super if icalc else core.mvsf.Super
    A = core.mv.Analyse if icalc else core.mvsf.Analyse
    C = core.mv.Compensate if icalc else core.mvsf.Compensate
    pad = max(blksize, 8)

    sup = ref or (c.std.Convolution(matrix=[1, 2, 1, 2, 4, 2, 1, 2, 1]) if blur > 0.0 else c)
    sup = S(sup, hpad=pad, vpad=pad, pel=pel, sharp=2)
    sup_rend = S(c, hpad=pad, vpad=pad, pel=pel, sharp=2, levels=1) if ref or blur > 0.0 else sup

    th1 = thsad
    th2 = (thsad + thsad2)/2 if radt==3 else thsad2
    th3 = thsad2

    bv1 = A(sup, isb=True, delta=1, blksize=blksize, overlap=overlap, chroma=chroma, truemotion=truemotion, pglobal=pglobal)
    fv1 = A(sup, isb=False, delta=1, blksize=blksize, overlap=overlap, chroma=chroma, truemotion=truemotion, pglobal=pglobal)
    if radt >= 2:
        bv2 = A(sup, isb=True, delta=2, blksize=blksize, overlap=overlap, chroma=chroma, truemotion=truemotion, pglobal=pglobal)
        fv2 = A(sup, isb=False, delta=2, blksize=blksize, overlap=overlap, chroma=chroma, truemotion=truemotion, pglobal=pglobal)
    if radt >= 3:
        bv3 = A(sup, isb=True, delta=3, blksize=blksize, overlap=overlap, chroma=chroma, truemotion=truemotion, pglobal=pglobal)
        fv3 = A(sup, isb=False, delta=3, blksize=blksize, overlap=overlap, chroma=chroma, truemotion=truemotion, pglobal=pglobal)

    bc1 = C(c, sup_rend, bv1, thsad=th1)
    fc1 = C(c, sup_rend, fv1, thsad=th1)
    if radt >= 2:
        bc2 = C(c, sup_rend, bv2, thsad=th2)
        fc2 = C(c, sup_rend, fv2, thsad=th2)
    if radt >= 3:
        bc3 = C(c, sup_rend, bv3, thsad=th3)
        fc3 = C(c, sup_rend, fv3, thsad=th3)

    ic =          core.std.Interleave([bc1, c, fc1])           if radt == 1 else \
             core.std.Interleave([bc2, bc1, c, fc1, fc2])      if radt == 2 else \
        core.std.Interleave([bc3, bc2, bc1, c, fc1, fc2, fc3])

    output = core.tmedian.TemporalMedian(ic, radius=radt)
    return output.std.SelectEvery(radt*2+1, radt)  # Return middle frame


# BM3D denoising method
def BM3D(clip: vs.VideoNode, sigma: float, gpuid: int, chroma: bool, ref: Optional[vs.VideoNode], m: int, block_step: int, bm_range: int, ps_range: int) -> vs.VideoNode:
    matrix = GetMatrix(clip)
    clean = clip.resize.Bicubic(format=vs.RGBS, matrix_in=matrix) if chroma else core.std.ShufflePlanes(clip, [0], vs.GRAY).fmtc.bitdepth(bits=32, dmode=1)
    if ref:
        ref = ref.resize.Bicubic(format=vs.RGBS, matrix_in=matrix) if chroma else core.std.ShufflePlanes(ref, [0], vs.GRAY).fmtc.bitdepth(bits=32, dmode=1)
    if gpuid >= 0:
        clean = core.bm3dcuda_rtc.BM3D(clean, sigma=sigma, ref=ref, device_id=gpuid, fast=False, block_step=block_step, bm_range=bm_range, ps_range=ps_range)
    else:
        clean = core.bm3dcpu.BM3D(clean, sigma=sigma, ref=ref, block_step=block_step, bm_range=bm_range, ps_range=ps_range)
    return ConvertToM(clean, clip, m)


# KnlMeansCL denoising method, useful for dark noisy scenes
def KnlMeans(clip: vs.VideoNode, d: int, a: int, h: int, gpuid: int, chroma: bool, ref: Optional[vs.VideoNode]) -> vs.VideoNode:
    if ref and ref.format != clip.format:
        ref = ref.resize.Bicubic(format=clip.format)
    device = dict(device_type="auto" if gpuid >= 0 else "cpu", device_id=max(0, gpuid))
    if clip.format.color_family == vs.GRAY:
        return clip.knlm.KNLMeansCL(d=d, a=a, h=h, channels="Y", rclip=ref, **device)
    elif ClipSampling(clip) == "444":
        return clip.knlm.KNLMeansCL(d=d, a=a, h=h, channels="YUV", rclip=ref, **device)
    else:
        clean = clip.knlm.KNLMeansCL(d=d, a=a, h=h, channels="Y", rclip=ref, **device)
        uv = clip.knlm.KNLMeansCL(d=d, a=a, h=h/2, channels="UV", rclip=ref, **device) if chroma else clean
        return core.std.ShufflePlanes(clips=[clean, uv], planes=[0, 1, 2], colorfamily=vs.YUV)


def ConvertToM(c: vs.VideoNode, src: vs.VideoNode, m: int) -> vs.VideoNode:
    if src.format.color_family == vs.GRAY:
        fmt = vs.GRAY32 if m == 4 else vs.GRAY16 if m == 3 or m == 2 else vs.GRAY8
    else:
        samp = ClipSampling(c)
        fmt = vs.YUV444PS if m == 4 else vs.YUV444P16 if m == 3 else vs.YUV420P16 if samp == "420" else vs.YUV422P16 if samp == "422" else vs.YUV444P16
    if c.format == fmt:
        return c
    elif c.format.color_family in [vs.YUV, vs.GRAY]:
        return c.resize.Bicubic(format=fmt)
    else:
        # Convert back while respecting ColorRange, Matrix, ChromaLocation, Transfer and Primaries. Note that setting range=1 sets _ColorRange=0 (reverse)
        fullRange = GetColorRange(c)
        # transfer = GetTransfer(c)
        # primaries = GetPrimaries(c)
        return c.resize.Bicubic(format=fmt, matrix=GetMatrix(c), chromaloc=GetChromaLoc(c), range = 1 if fullRange == 0 else 0)


# Adjusts brightness and contrast
def Tweak(clip: vs.VideoNode, bright: float = None, cont: float = None) -> vs.VideoNode:
    fullRange = GetColorRange(clip)
    bd = clip.format.bits_per_sample
    isFLOAT = clip.format.sample_type == vs.FLOAT
    isGRAY = clip.format.color_family == vs.GRAY
    mid = 0 if isFLOAT else 1 << (bd - 1)

    if clip.format.color_family == vs.RGB:
        raise TypeError("Tweak: RGB color family is not supported!")

    if not (bright is None and cont is None):
        bright = 0.0 if bright is None else bright
        cont = 1.0 if cont is None else cont

        if isFLOAT:
            expr = "x {} * {} + 0.0 max 1.0 min".format(cont, bright)
            clip =  core.std.Expr([clip], [expr] if isGRAY else [expr, ''])
        else:
            luma_lut = []
            luma_min = 16  << (bd - 8) if not fullRange else 0
            luma_max = 235 << (bd - 8) if not fullRange else (1 << bd) - 1

            for i in range(1 << bd):
                val = int((i - luma_min) * cont + bright + luma_min + 0.5)
                luma_lut.append(min(max(val, luma_min), luma_max))

            clip = core.std.Lut(clip, [0], luma_lut)
    return clip


# from muvsfunc
def Sharpen(clip: vs.VideoNode, amountH: float = 1.0, amountV: Optional[float] = None, planes = None) -> vs.VideoNode:
    # Avisynth's internel filter Sharpen()
    funcName = 'Sharpen'
    if amountH < -1.5849625 or amountH > 1:
        raise ValueError(funcName + ': \'amountH\' have not a correct value! [-1.58 ~ 1]')

    if amountV is None:
        amountV = amountH
    else:
        if amountV < -1.5849625 or amountV > 1:
            raise ValueError(funcName + ': \'amountV\' have not a correct value! [-1.58 ~ 1]')

    planes = list(range(clip.format.num_planes)) if planes is None else planes

    center_weight_v = math.floor(2 ** (amountV - 1) * 1023 + 0.5)
    outer_weight_v = math.floor((0.25 - 2 ** (amountV - 2)) * 1023 + 0.5)
    center_weight_h = math.floor(2 ** (amountH - 1) * 1023 + 0.5)
    outer_weight_h = math.floor((0.25 - 2 ** (amountH - 2)) * 1023 + 0.5)

    conv_mat_v = [outer_weight_v, center_weight_v, outer_weight_v]
    conv_mat_h = [outer_weight_h, center_weight_h, outer_weight_h]

    if math.fabs(amountH) >= 0.00002201361136: # log2(1+1/65536)
        clip = core.std.Convolution(clip, conv_mat_v, planes=planes, mode='v')

    if math.fabs(amountV) >= 0.00002201361136:
        clip = core.std.Convolution(clip, conv_mat_h, planes=planes, mode='h')
    return clip


def ClipSampling(clip: vs.VideoNode) -> str:
    return "GRAY" if clip.format.color_family == vs.GRAY else \
            "444" if clip.format.subsampling_w == 0 and clip.format.subsampling_h == 0 else \
            "422" if clip.format.subsampling_w == 1 and clip.format.subsampling_h == 0 else "420"

# Get frame properties
def GetFrameProp(c: vs.VideoNode, name: str, default):
    props = c.get_frame(0).props
    return props[name] if name in props else default

def GetColorRange(c: vs.VideoNode) -> int:
    return GetFrameProp(c, "_ColorRange", 1)

def GetMatrix(c: vs.VideoNode) -> int:
    matrix = GetFrameProp(c, "_Matrix", 1)
    return 6 if matrix in [0, 2] else matrix

def GetTransfer(c: vs.VideoNode) -> int:
    transfer = GetFrameProp(c, "_Transfer", 0)
    return GetMatrix(c) if transfer in [0, 2] else transfer

def GetPrimaries(c: vs.VideoNode) -> int:
    primaries = GetFrameProp(c, "_Primaries", 0)
    return GetMatrix(c) if primaries in [0, 2] else primaries

def GetChromaLoc(c: vs.VideoNode) -> int:
    return GetFrameProp(c, "_ChromaLocation", 0)
