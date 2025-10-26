from typing import Any, Callable
import pandas as pd


type RegCallable = Callable[[pd.Series, list[dict[str,Any]]], list[str]]

def parse_bit_range(bit_str):
    """Parse bit or bit range like '7:0' -> (7, 0)."""
    if ':' in str(bit_str):
        hi, lo = map(int, bit_str.split(':'))
    else:
        hi = lo = int(bit_str)
    return hi, lo

def generate_bitfields(df_bits:pd.DataFrame, width:int) -> list[dict[str,Any]]:
    """Generate C++ bitfields from the bit description dataframe for one register."""
    fields = []
    used_bits = set()

    for _, row in df_bits.iterrows():
        mnemonic = str(row['Mnemonic']).strip()
        bit = str(row['Bit']).strip()
        desc = str(row.get('Description', '')).strip()
        hi, lo = parse_bit_range(bit)
        nbits = hi - lo + 1
        fields.append({
            'name': mnemonic,
            'bits': nbits,
            'desc': desc,
            'hi': hi,
            'lo': lo
        })
        used_bits.update(range(lo, hi + 1))

    # Fill any gaps as RESERVED
    all_bits = set(range(width))
    unused = sorted(all_bits - used_bits)
    if unused:
        reserved_groups = []
        start = prev = unused[0]
        # raise NotImplementedError()
        for b in unused[1:] + [None]:
            if b is None or b != prev + 1: # type: ignore
                reserved_groups.append((start, prev))
                if b is not None:
                    start = b
            prev = b

        for hi, lo in reserved_groups:
            fields.append({
                'name': 'RESERVED',
                'bits': hi - lo + 1,
                'desc': 'Reserved',
                'hi': hi,
                'lo': lo
            })

    fields = sorted(fields, key=lambda f: f['hi'], reverse=True)
    return fields

def get_uint_type(width:int) -> str:
    """Return correct uint type name for bit width."""
    if width <= 8:
        return "uint8_t"
    elif width <= 16:
        return "uint16_t"
    elif width <= 32:
        return "uint32_t"
    else:
        return "uint64_t"
    
def generate_assignment_lines(bitfields, width):
    """Generate C++ lines for operator= assignments."""
    lines = []
    # lines.append(f"    {get_uint_type(width)} mask = 1; // ensure correct width")

    just_names = map(lambda x: x["name"], bitfields)
    max_name_width= max(map(len, just_names))

    for bf in bitfields:  # Reverse for bit numbering from MSB->LSB
        hi, lo = bf['hi'], bf['lo']
        name = bf['name']
        if bf['bits'] == 1:
            lines.append(f"        {name:<{max_name_width}} = (n >> {lo}) & 0x1;")
        else:
            mask = hex((1 << bf['bits']) - 1)
            lines.append(f"        {name:<{max_name_width}} = (n >> {lo}) & {mask};")
    
    return "\n".join(lines)

def generate_cpp_struct(reg_row:pd.Series, bitfields:list[dict[str,Any]]) -> list[str]:
    """Generate C++ struct for one register."""
    name = reg_row['Register Name']
    addr = reg_row['Address']
    width = int(reg_row['Width (bits)'])
    reset = reg_row['Reset Value']
    reg_type = reg_row['Type']
    desc = reg_row['Description']
    
    lines = []
    lines.append(f"// {name} ({addr}): {desc}")
    lines.append(f"// Type: {reg_type}, Width: {width} bits, Reset: {reset}")
    lines.append(f"struct {name} {{")

    just_names = map(lambda x: x["name"], bitfields)
    max_name_width= max(map(len, just_names))
    
    for bf in bitfields:
        comment = f"    // {bf['desc']}" if bf['desc'] else ""
        # lines.append(f"    uint{width}_t {bf['name']} : {bf['bits']}; {comment}")
        lines.append(f"    {get_uint_type(width)} {bf['name']:<{max_name_width}} : {bf['bits']}; {comment}")
    
    # operator= overload
    lines.append("")
    lines.append(f"    {name}& operator=({get_uint_type(width)} n) {{")
    lines.append(generate_assignment_lines(bitfields, width))
    lines.append("        return *this;")
    lines.append("    };")

    lines.append("};\n")
    # lines.reverse()
    return lines

def generate_header_requirements() -> list[str]:
    return [
        "#pragma once",
        "#include <stdint.h>\n",
        "// Auto-generated register definitions\n"
    ]

def generate_main_register_space(enum_entries: list[tuple[str,str,str]]) -> list[str]:
    # Add Register enum
    just_names = map(lambda x: x[0], enum_entries)
    max_name_width= max(map(len, just_names))
    header_lines = []
    header_lines.append("// Register map enum")
    header_lines.append("enum class Register : uint32_t {")
    for name, addr, desc in enum_entries:
        header_lines.append(f"    {name:<{max_name_width}} = 0x{addr}, // {desc}")
    header_lines.append("};\n")

    return header_lines

def excel_to_cpp_header(config:dict[str,Any]):
    excel_file:str = config.get("excel_file") # type: ignore
    output_file:str = config.get("output_file") # type: ignore

    if excel_file is None or output_file is None:
        raise ValueError()

    language:str = config.get("language","cpp")

    # Read all sheets
    sheets = pd.read_excel(excel_file, 
                           sheet_name=["Register Map","Register Specific"],
                           skiprows=1)

    
    header_lines = []

    

    main_register_creation:dict[str,RegCallable] = {
        "cpp": generate_cpp_struct
    }

    missing_register_format:dict[str,str] = {
        "cpp": "// Missing sheet for {reg_name}\n"
    }

    if language == "cpp":
        header_lines.extend(generate_header_requirements())

    enum_entries:list[tuple[str,str,str]] = []

    all_registers = sheets["Register Specific"]

    for (index, reg) in sheets["Register Map"].iterrows():
        reg_name:str = reg['Register Name']
        width:int = int(reg['Width (bits)'])
        addr:str = reg['Address']
        desc:str = reg['Description']
        if any(all_registers["Register"] == reg_name):
            bit_df = all_registers[all_registers["Register"] == reg_name]
            bitfields = generate_bitfields(bit_df, width)

            reg_text = main_register_creation.get(language)(reg, bitfields) # type: ignore
            header_lines.extend(reg_text)

        else:
            header_lines.append(missing_register_format.get(language).format(reg_name=reg_name)) # type: ignore
        enum_entries.append((reg_name, addr, desc))
    
    if language == "cpp":
        main_registers = generate_main_register_space(enum_entries)
        header_lines.extend(main_registers)
    
    with open(output_file, 'w') as f:
        f.write("\n".join(header_lines))
    
    print(f"✅ Generated: {output_file}")

    return output_file

if __name__ == "__main__":
    config = {
        "excel_file": "/home/joel/Documents/PlatformIO/Projects/Lights/Lights-MCU/docs/Register Map.ods",
        "output_file": "/home/joel/Documents/PlatformIO/Projects/Lights/Lights-MCU/test/python_testing/registers.h"
    }
    excel_to_cpp_header(config)

