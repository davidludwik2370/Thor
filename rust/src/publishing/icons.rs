//! PNG/ICO normalization and platform icon containers, without Pillow.
use crate::diagnostic::{Error, Result};
use image::{
    codecs::{
        ico::{IcoEncoder, IcoFrame},
        png::PngEncoder,
    },
    imageops::{self, FilterType},
    ExtendedColorType, ImageEncoder, ImageFormat, RgbaImage,
};
use std::{
    fs,
    io::{Cursor, Write},
    path::Path,
};
fn failure(path: &Path, error: impl std::fmt::Display) -> Error {
    Error::new(format!(
        "Cannot read icon {}: {error}",
        path.file_name().unwrap_or_default().to_string_lossy()
    ))
}
fn ico_frame(data: &[u8]) -> std::result::Result<Vec<u8>, String> {
    if data.get(..4) != Some(&[0, 0, 1, 0]) {
        return Err("expected ICO image data".into());
    }
    let count = data
        .get(4..6)
        .and_then(|bytes| bytes.try_into().ok())
        .map(u16::from_le_bytes)
        .ok_or("truncated ICO header")? as usize;
    let mut best = None;
    for index in 0..count {
        let entry = data
            .get(6 + 16 * index..22 + 16 * index)
            .ok_or("truncated ICO directory")?;
        let width = if entry[0] == 0 {
            256
        } else {
            u32::from(entry[0])
        };
        let height = if entry[1] == 0 {
            256
        } else {
            u32::from(entry[1])
        };
        let bits = u16::from_le_bytes(entry[6..8].try_into().unwrap());
        let score = (width * height, bits);
        if best.as_ref().is_none_or(|(old, _)| score > *old) {
            best = Some((score, entry));
        }
    }
    let (_, entry) = best.ok_or("ICO directory contains no image")?;
    let length = u32::from_le_bytes(entry[8..12].try_into().unwrap()) as usize;
    let start = u32::from_le_bytes(entry[12..16].try_into().unwrap()) as usize;
    let content = data
        .get(
            start
                ..start
                    .checked_add(length)
                    .ok_or("ICO image range overflows")?,
        )
        .ok_or("truncated ICO image")?;
    let mut result = vec![0, 0, 1, 0, 1, 0];
    result.extend_from_slice(&entry[..12]);
    result.extend_from_slice(&22u32.to_le_bytes());
    result.extend_from_slice(content);
    Ok(result)
}
pub fn normalize(path: &Path, warnings: &mut Vec<String>) -> Result<RgbaImage> {
    let extension = path
        .extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_lowercase();
    let format = match extension.as_str() {
        "png" => ImageFormat::Png,
        "ico" => ImageFormat::Ico,
        _ => return Err(Error::new("Application icons must be PNG or ICO files.")),
    };
    let mut bytes = fs::read(path).map_err(|error| failure(path, error))?;
    if image::guess_format(&bytes).ok() != Some(format) {
        return Err(failure(
            path,
            format!("expected {} image data", extension.to_uppercase()),
        ));
    }
    if format == ImageFormat::Ico {
        bytes = ico_frame(&bytes).map_err(|error| failure(path, error))?;
    }
    let mut reader = image::ImageReader::with_format(Cursor::new(&bytes), format);
    reader.no_limits();
    let (width, height) = reader
        .into_dimensions()
        .map_err(|error| failure(path, error))?;
    if u64::from(width) * u64::from(height) > 178956970 {
        return Err(failure(path, "image exceeds decompression limit"));
    }
    let mut reader = image::ImageReader::with_format(Cursor::new(&bytes), format);
    reader.no_limits();
    let image = reader
        .decode()
        .map_err(|error| failure(path, error))?
        .into_rgba8();
    let side = width.min(height);
    let mut left = (width - side) / 2;
    let mut top = (height - side) / 2;
    let mut visible: Option<(u32, u32, u32, u32)> = None;
    for (x, y, pixel) in image.enumerate_pixels() {
        if pixel[3] != 0 {
            visible = Some(match visible {
                None => (x, y, x + 1, y + 1),
                Some((a, b, c, d)) => (a.min(x), b.min(y), c.max(x + 1), d.max(y + 1)),
            });
        }
    }
    if let Some((a, b, c, d)) = visible {
        if c - a <= side {
            left = left.max(c.saturating_sub(side)).min(a);
        }
        if d - b <= side {
            top = top.max(d.saturating_sub(side)).min(b);
        }
    }
    let cropped = visible
        .is_some_and(|(a, b, c, d)| a < left || b < top || c > left + side || d > top + side);
    let square = if width != height {
        imageops::crop_imm(&image, left, top, side, side).to_image()
    } else {
        image
    };
    let result = if side > 1024 {
        imageops::resize(&square, 1024, 1024, FilterType::Lanczos3)
    } else {
        square
    };
    if cropped {
        warnings.push(format!("Icon {} was center-cropped from {width}x{height} to {side}x{side}; visible pixels were removed.",path.file_name().unwrap_or_default().to_string_lossy()));
    }
    Ok(result)
}
fn png(image: &RgbaImage) -> Result<Vec<u8>> {
    let mut bytes = vec![];
    PngEncoder::new(&mut bytes)
        .write_image(
            image.as_raw(),
            image.width(),
            image.height(),
            ExtendedColorType::Rgba8,
        )
        .map_err(|error| Error::new(format!("Cannot encode application icon: {error}")))?;
    Ok(bytes)
}
pub fn write(image: &RgbaImage, source: &Path, destination: &Path, system: &str) -> Result<()> {
    if system == "windows"
        && source
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("ico"))
    {
        let raw = fs::read(source)?;
        let selected = ico_frame(&raw).map_err(|error| failure(source, error))?;
        let original = image::load_from_memory_with_format(&selected, ImageFormat::Ico)
            .map_err(|error| failure(source, error))?;
        if original.width() == image.width()
            && original.height() == image.height()
            && image.width() <= 256
        {
            fs::copy(source, destination)?;
            return Ok(());
        }
    }
    if system == "darwin" {
        let base = imageops::resize(image, 1024, 1024, FilterType::Lanczos3);
        let kinds = [
            (b"ic07", 128),
            (b"ic08", 256),
            (b"ic09", 512),
            (b"ic10", 1024),
            (b"ic11", 32),
            (b"ic12", 64),
            (b"ic13", 256),
            (b"ic14", 512),
        ];
        let mut images = std::collections::BTreeMap::new();
        for (_, size) in kinds {
            if let std::collections::btree_map::Entry::Vacant(entry) = images.entry(size) {
                entry.insert(png(&imageops::resize(
                    &base,
                    size,
                    size,
                    FilterType::CatmullRom,
                ))?);
            }
        }
        let total = 8
            + 8
            + 8 * kinds.len()
            + kinds
                .iter()
                .map(|(_, size)| 8 + images[size].len())
                .sum::<usize>();
        let mut file = fs::File::create(destination)?;
        file.write_all(b"icns")?;
        file.write_all(&(total as u32).to_be_bytes())?;
        file.write_all(b"TOC ")?;
        file.write_all(&((8 + 8 * kinds.len()) as u32).to_be_bytes())?;
        for (kind, size) in kinds {
            file.write_all(kind)?;
            file.write_all(&((8 + images[&size].len()) as u32).to_be_bytes())?;
        }
        for (kind, size) in kinds {
            file.write_all(kind)?;
            file.write_all(&((8 + images[&size].len()) as u32).to_be_bytes())?;
            file.write_all(&images[&size])?;
        }
    } else if system == "windows" {
        let base = imageops::resize(image, 256, 256, FilterType::Lanczos3);
        let mut frames = vec![];
        for size in [16, 24, 32, 48, 64, 128, 256] {
            let frame = imageops::resize(&base, size, size, FilterType::Lanczos3);
            frames.push(
                IcoFrame::as_png(frame.as_raw(), size, size, ExtendedColorType::Rgba8)
                    .map_err(|error| Error::new(error.to_string()))?,
            );
        }
        IcoEncoder::new(fs::File::create(destination)?)
            .encode_images(&frames)
            .map_err(|error| Error::new(error.to_string()))?;
    } else {
        fs::write(destination, png(image)?)?;
    }
    Ok(())
}
