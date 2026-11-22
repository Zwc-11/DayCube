"""Items: every thing seen in a video or a scan, as an entry in the 3D world you can search for.

A recording becomes a memory index in four steps (``run.py``):

1. **Frames** (``frames.py``). A LiDAR scan gives frames with ARKit's camera pose and a depth map;
   a plain video gives shots, a keyframe per shot and a depth map from a model.
2. **Detections** (``detect.py``). An open-vocabulary detector names and boxes what is in each
   frame; the same model captions the frame and reads its text, which is the frame's own entry in
   the index.
3. **Observations** (``lift.py``, ``embed.py``). Each box gets a place (its depth carried through the
   camera into the world), a size, and an image embedding that text can be compared with.
4. **Items** (``merge.py``). Observations of the same thing from different frames are joined: near
   each other in space and alike in their embeddings. An item keeps where it is, how sure that is
   (the spread of its observations), when it was seen, and its best picture.

``store.py`` writes ``vault/<date>/items/<id>/`` (``items.json``, ``vectors.f16``, ``crops/``) and
``search.py`` answers "where did I see a bench" from the vectors and the labels.

Nothing here is trained: the detector, the embedder and the depth model are open models used as
they are (``models.py`` names each and its licence). What is stored is derived from the recording
and labelled with how its depth was known: ``lidar`` (measured), ``estimate`` (ARKit's guess beyond
the LiDAR's range) or ``model`` (a depth model's).
"""
