"""Independent EdgeTAM sessions for one selected source character. No downloads."""
import torch

def _device(choice):
    if choice != 'auto':
        return choice
    if torch.cuda.is_available():
        return 'cuda'
    if getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
        return 'mps'
    return 'cpu'

def _track_one(model, processor, video_u8, prompts, device):
    """One object, own session. Returns float mask logits [T, H, W] on CPU."""
    frames, height, width = video_u8.shape[:3]
    session = processor.init_video_session(video=video_u8, inference_device=device, video_storage_device='cpu', processing_device='cpu', dtype=torch.float32)
    logits = torch.full((frames, height, width), -10000.0)

    def keep(output):
        m = processor.post_process_masks([output.pred_masks.float().cpu()], original_sizes=[[height, width]], binarize=False)[0]
        logits[output.frame_idx] = m[0, 0].float().cpu()
    for frame, box in prompts:
        processor.add_inputs_to_inference_session(inference_session=session, frame_idx=frame, obj_ids=1, input_boxes=[[box]])
        keep(model(inference_session=session, frame_idx=frame))
    first, last = (prompts[0][0], prompts[-1][0])
    with torch.inference_mode():
        for output in model.propagate_in_video_iterator(session, start_frame_idx=first):
            keep(output)
        for output in model.propagate_in_video_iterator(session, start_frame_idx=last, reverse=True):
            if output.frame_idx < first:
                keep(output)
    return logits
