from PIL import Image, ImageDraw

def create_square_ratio_bar(num1, num2, filename, width=350, height=50, color1=(25, 131, 240), color2=(240, 132, 25), grey=(192, 192, 192)):
    # Calculate the total and the ratios
    total = num1 + num2
    if total == 0:
        ratio1 = ratio2 = 0
    else:
        ratio1 = num1 / total
        ratio2 = num2 / total

    # Calculate the segment widths
    segment1_width = int(width * ratio1)
    segment2_width = int(width * ratio2)

    # Calculate the exact positions for the segments with overlap
    left_end = segment1_width
    right_start = width - segment2_width


    # Create a new image with a transparent background
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw the first segment
    if segment1_width > 0:
        draw.rectangle([0, 0, left_end, height], fill=color1)

    # Draw the second segment
    if segment2_width > 0:
        draw.rectangle([right_start, 0, width, height], fill=color2)

    if segment1_width == 0 and segment2_width == 0:
        draw.rectangle([0, 0, width, height], fill=grey)

    img.save(filename, format="png")


