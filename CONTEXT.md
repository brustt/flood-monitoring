# flood-monitoring

Maps the extent of a flood from a time series of Sentinel-1 radar images and prepares the result for showing on the brustt.xyz Platform.

## Language

Terms shared with the Platform (**Project**, **Event**, **Layer**, **Published Result**, **Publish**, **Publish Contract**) keep the meaning given in the `brustt-platform` glossary. Terms shared with `eo-stac` (**Item**, **Subset**) and `eo-pipe` (**Tile stack**) keep theirs.

### Input

**Event**:
One real-world flood bounded in place and time, named `<place>-<yyyy-mm>` (e.g. `saintes-2026-02`). Everything the pipeline computes belongs to one Event.
_Avoid_: case, run, study area

**Area of Interest**:
The rectangle on the ground an Event is studied over. All rasters of an Event cover it.
_Avoid_: bbox, footprint, zone

**Acquisition**:
One Sentinel-1 pass over the Area of Interest, in both polarizations (VV and VH), on one date.
_Avoid_: image, scene, product

**Series**:
All Acquisitions of an Event, from the same orbit, in time order. It starts well before the flood so that normal conditions are known.
_Avoid_: stack (the file), time series (the plot)

**Cube**:
The Series held as one gridded dataset with a time axis and one variable per polarization. The Filtered Cube is the Cube after speckle reduction.
_Avoid_: stack, datacube, tile stack

### Detection

**Baseline**:
The normal backscatter of each pixel, estimated from the Acquisitions before the flood.
_Avoid_: reference, background

**Drop**:
How far a pixel's VV backscatter falls below its Baseline, in dB. Open water reflects the radar away, so a flooded pixel shows a large Drop.
_Avoid_: change, anomaly, difference

**Event Start**:
The first Acquisition of the Series in which the flood is visible over the Area of Interest. One date for the whole area, not one per pixel.
_Avoid_: onset, trigger date, flood date

**Post-event Acquisition**:
An Acquisition at or after the Event Start.

**Water Mask**:
The pixels of one Acquisition classified as open water, whatever the cause (river, lake or flood).
_Avoid_: flood mask, water extent

**Permanent Water**:
Pixels that are water before the Event Start: rivers, lakes, ponds.
_Avoid_: reference water, normal water

**Flood Extent**:
The pixels that are water in at least one Post-event Acquisition and are not Permanent Water. The maximum reach of the flood. Computed and reported (`flood_extent_km2`) but not published.
_Avoid_: flood mask, water mask, prediction

**Water Footprint**:
The pixels that are water in at least one Post-event Acquisition, Permanent Water included. The river and the flood together, and the Project's `result` Layer.
_Avoid_: identified water mask, flood mask, water extent

**Peak**:
The Post-event Acquisition with the largest flooded area.

### Output

**Composite**:
A three-band picture of one Acquisition made from VV, VH and their ratio, all in dB, for visual reading. The Before Composite is the last Acquisition before the Event Start; the After Composite is the Peak.
_Avoid_: RGB, false colour, quicklook

## Relationships

- An **Event** has one **Area of Interest** and one **Series**
- A **Series** is held as a **Cube**; each time step is one **Acquisition**
- The **Baseline** comes from the Acquisitions before the **Event Start**; the **Drop** is measured against it
- Each **Post-event Acquisition** gives one **Water Mask**
- **Water Footprint** = union of post-event **Water Masks**
- **Flood Extent** = **Water Footprint** minus **Permanent Water**
- The **Water Footprint** and the two **Composites** are the **Layers** of the Event's **Published Result**

## Flagged ambiguities

- "flood mask" was used for both the water of one date and the final result: resolved. **Water Mask** is per Acquisition and includes the river; **Water Footprint** is the union of the post-event Water Masks, river included, and is the single published result; **Flood Extent** excludes **Permanent Water** and is only reported in the summary.
- "Event Start" is not the `start` date shown on the Platform. The Platform's Event window is set by hand to the calendar period of the flood; **Event Start** is detected from the Series and only decides which Acquisitions are post-event.
- The Project is called "flood detection" in Platform documents; its slug is `flood-monitoring`.
