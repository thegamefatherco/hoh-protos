"""Bundled protobuf descriptor data for gamedesign decoding."""

from importlib import resources

from google.protobuf import descriptor_pb2


def load_well_known_fds() -> descriptor_pb2.FileDescriptorSet:
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(
        resources.files("xapk_to_proto.data").joinpath("well_known.pb").read_bytes()
    )
    return fds
